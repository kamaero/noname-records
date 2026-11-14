"""Номер связывает подход с главой, а полноту чтения доказывает отдельная сверка."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.services.asr_align import normalize_words, word_stream

APPROACH_VERSION = 2


def _expressive(text):
    # Тройные гласные — манера чтения, но двойные согласные бывают частью имени.
    return re.sub(r"([аеиоуыэюяaeiou])\1{2,}", r"\1", str(text).lower())


def _stream(spoken, *, language="ru"):
    result = []
    for segment in spoken:
        if not segment.get('words'):
            result.extend(word_stream([segment], language=language))
            continue
        block = []
        for item in segment['words']:
            text = str(item.get('word') or '')
            if re.fullmatch(r'\s*\[[^\]]*\]\s*', text):
                continue
            if item.get('_timing_unresolved'):
                if block:
                    result.extend(word_stream([{'words': block}], language=language))
                    block = []
                result.append(('__timing_unknown__', float(item['start']), float(item['end'])))
            else:
                block.append({**item, 'word': _expressive(text)})
        if block:
            # Английские даты и время зависят от соседних слов. Передача
            # каждого слова отдельно потеряла бы действующую нормализацию BYOK.
            result.extend(word_stream([{'words': block}], language=language))
    return result


def _prefix_variants(index, language):
    from app.services.chapter_title_match import _variants

    if type(index) is not int or not 0 <= index < 10000:
        return []
    if language == 'ru':
        return _variants(f'Глава {index}', index, language=language)
    suffix = ('th' if 10 <= index % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(index % 10, 'th'))
    return [normalize_words(f'Chapter {index}', language=language),
            normalize_words(f'Chapter {index}{suffix}', language=language)]


def match_approaches(candidates, spoken, *, language="ru"):
    from app.services.chapter_title_match import match_title_candidates

    stream = _stream(spoken, language=language)
    # Старый точный поиск остаётся для заголовков без числового префикса.
    prefix_word = 'глава' if language == 'ru' else 'chapter'
    plain = [item for item in candidates if normalize_words(' '.join(item['lines']), language=language)[:1] != [prefix_word]]
    # Нулевое лексическое слово разделяет точный поиск: его удаление
    # склеило бы соседние слова в заголовок, которого диктор не произносил.
    blocks, block = [], []
    for word, start, end in stream:
        if end <= start or word == '__timing_unknown__':
            if block:
                blocks.append(block)
            block = []
        else:
            block.append({'words': [{'word': word, 'start': start, 'end': end}]})
    if block:
        blocks.append(block)
    exact = {'intervals': []}
    for block in blocks if plain else []:
        exact['intervals'].extend(match_title_candidates(plain, block, language=language)['intervals'])
    words = [w for w, _, _ in stream]
    targets, prefixes = {}, {}
    # Неизвестный номер тоже разрывает контекст: «двадцать» не должен поглотить
    # «двадцать первую», даже если в списке целей есть только двадцатая глава.
    indices = set(range(1, 101)) | {c.get('chapter_index') for c in candidates if type(c.get('chapter_index')) is int}
    for index in indices:
        for prefix in _prefix_variants(index, language):
            prefixes[tuple(prefix)] = index
    for candidate in candidates:
        text = ' '.join(candidate['lines'])
        for prefix in _prefix_variants(candidate.get('chapter_index'), language):
            target = normalize_words(_expressive(text), language=language)
            if target[:len(prefix)] == list(prefix):
                targets.setdefault(candidate['chapter_index'], []).append((candidate, target[len(prefix):], len(prefix)))
                break
    anchors = []
    for pos, word in enumerate(words):
        if word != prefix_word:
            continue
        found = [(len(prefix), index) for prefix, index in prefixes.items() if words[pos:pos + len(prefix)] == list(prefix)]
        if found:
            length, index = max(found)
            anchors.append((pos, pos + length, index))
        else:
            anchors.append((pos, pos + 1, None))
    intervals = list(exact['intervals'])
    used = set()
    for interval in intervals:
        used.update(i for i, (_, s, e) in enumerate(stream) if s >= interval['start'] and e <= interval['end'])
    for anchor_number, (left, body, index) in enumerate(anchors):
        options = targets.get(index, [])
        if len(options) != 1:
            continue
        candidate, suffix, _ = options[0]
        right = anchors[anchor_number + 1][0] if anchor_number + 1 < len(anchors) else len(stream)
        line_indices = [i for i, text in enumerate(candidate['lines']) if normalize_words(text, language=language)]
        if not suffix:
            intervals.append({**candidate, 'text': ' '.join(candidate['lines']), 'line_index': line_indices[0],
                'line_indices': line_indices, 'start': stream[left][1], 'end': stream[body - 1][2],
                'score': 1., 'review_only': False})
            used.update(range(left, body))
            continue
        cursor = body
        while cursor < right:
            choices = []
            for size in range(max(1, len(suffix) - 2), len(suffix) + 3):
                end = cursor + size
                if end > right:
                    continue
                # Подход не заканчивается посреди непрерывной чужой фразы.
                if end < right and words[end] != '__timing_unknown__' and stream[end][1] - stream[end - 1][2] < .5:
                    continue
                said = words[cursor:end]
                if '__timing_unknown__' in said:
                    continue
                score = SequenceMatcher(None, ' '.join(suffix), ' '.join(said), autojunk=False).ratio()
                if score >= .70 and (len(suffix) > 2 or score >= .84):
                    choices.append((score, -abs(size - len(suffix)), end))
            if not choices:
                cursor += 1
                continue
            score, _, end = max(choices)
            first = left if cursor == body else cursor
            # Длинная пауза либо посторонняя речь между номером и названием
            # не дают права объявить сокращённый повтор полным заголовком.
            full = cursor == body and stream[body][1] - stream[body - 1][2] <= 3.
            if not full:
                first = cursor
                rivals = [SequenceMatcher(None, ' '.join(other_suffix), ' '.join(words[cursor:end]),
                    autojunk=False).ratio() for other_index, entries in targets.items() if other_index != index
                    for _other, other_suffix, _length in entries]
                if rivals and max(rivals) >= score - .08:
                    cursor = end
                    continue
            if stream[end - 1][2] <= stream[first][1]:
                cursor = end
                continue
            intervals.append({**candidate, 'text': ' '.join(candidate['lines']),
                'line_index': line_indices[0], 'line_indices': line_indices,
                'start': stream[first][1], 'end': stream[end - 1][2], 'score': score,
                'review_only': not full or score < 1.,
                'observed_text': ' '.join(words[first:end])})
            used.update(range(first, end))
            cursor = end
    intervals.sort(key=lambda item: (item['start'], item['end'], item['chapter_id']))
    counts = {}
    for interval in intervals:
        key = interval['chapter_id']
        counts[key] = counts.get(key, 0) + 1
        interval['take_index'] = counts[key]
    unresolved = []
    cursor = 0
    while cursor < len(stream):
        if cursor in used:
            cursor += 1
            continue
        end = cursor + 1
        while end < len(stream) and end not in used and stream[end][1] - stream[end - 1][2] < 1.:
            end += 1
        unresolved.append({'start': stream[cursor][1], 'end': stream[end - 1][2],
            'text': ' '.join(words[cursor:end]), 'reason': 'unmatched_speech', 'candidates': []})
        cursor = end
    for interval in intervals:
        if interval.get('review_only'):
            unresolved.append({**interval, 'reason': 'title_approach_review', 'candidates': []})
    confirmed = {i['chapter_id'] for i in intervals if not i.get('review_only')}
    missing = [{**c, 'text': ' '.join(c['lines']), 'line_index': 0,
                'line_indices': list(range(len(c['lines'])))} for c in candidates if c['chapter_id'] not in confirmed]
    return {'intervals': intervals, 'unresolved': unresolved, 'missing': missing}

