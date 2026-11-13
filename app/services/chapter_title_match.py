"""Короткие заголовки требуют точного текста и самостоятельного подхода."""
from __future__ import annotations

import math

from app.services.asr_align import normalize_words, number_words, ordinal_words, word_stream

MIN_BOUNDARY_PAUSE = 0.5


def _variants(text: str, chapter_index, *, language="ru") -> list[list[str]]:
    target = normalize_words(text, language=language)
    if language != 'ru' or type(chapter_index) is not int or not 0 <= chapter_index < 10000 or target[:1] != ['глава']:
        return [target]
    ordinal = ordinal_words(chapter_index)
    last = ordinal[-1]
    feminine = ('третья' if last == 'третий' else last[:-2] + 'ая'
                if last.endswith(('ый', 'ой', 'ий')) else last)
    numbers = [number_words(chapter_index), ordinal, [*ordinal[:-1], feminine]]
    # Заменяем только подтверждённый числовой префикс этой главы. Слова названия
    # после номера остаются точными, даже если сами содержат другие числа.
    for number in sorted(numbers, key=len, reverse=True):
        if target[1:1 + len(number)] == number:
            tail = target[1 + len(number):]
            return list(dict.fromkeys(tuple(['глава', *value, *tail]) for value in numbers))
    return [target]


def match_title_candidates(candidates: list[dict], spoken: list[dict], *, language: str = "ru") -> dict:
    """Не угадывать главу по порядку: одинаковые заголовки остаются спорными.

    Точное сравнение после общей нормализации чисел не позволяет нечёткому
    совпадению заменить номер главы. Каждый повтор сохраняет собственный интервал.
    """
    expected, seen = [], set()
    for candidate in candidates:
        cid = candidate.get('chapter_id')
        if not isinstance(cid, str) or not cid.strip() or cid in seen:
            raise ValueError('chapter_id должен быть уникальной непустой строкой')
        seen.add(cid)
        lines = candidate.get('lines')
        if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
            raise ValueError('lines должен быть списком строк')
        indices = [index for index, text in enumerate(lines) if normalize_words(text, language=language)]
        if indices:
            expected.append({'chapter_id': cid, 'line_index': indices[0], 'line_indices': indices,
                             'text': ' '.join(lines[index] for index in indices),
                             'chapter_index': candidate.get('chapter_index')})
    expected.sort(key=lambda item: (item['chapter_id'], item['line_index']))
    stream = word_stream(spoken, language=language)
    previous_end = 0.0
    for _, start, end in stream:
        if not math.isfinite(start) or not math.isfinite(end) or start < previous_end or end <= start:
            raise ValueError('ASR должен содержать возрастающие неперекрывающиеся тайминги')
        previous_end = end
    words = [word for word, _, _ in stream]
    matches = []
    for line in expected:
        for target in _variants(line['text'], line.get('chapter_index'), language=language):
            target = list(target)
            for start in range(len(words) - len(target) + 1):
                end = start + len(target)
                if words[start:end] == target:
                    matches.append({**line, 'first': start, 'last': end})
    # «Глава двадцать» — не самостоятельная глава внутри «Глава двадцать один».
    # Иначе круглый номер создаёт ложную конкуренцию со всеми составными числами.
    matches = [item for item in matches if not any(
        other['first'] == item['first'] and other['last'] > item['last']
        and type(item.get('chapter_index')) is int and type(other.get('chapter_index')) is int
        and item['chapter_index'] != other['chapter_index']
        and normalize_words(item['text'], language=language) in [list(value) for value in _variants(
            f"Глава {item['chapter_index']}", item['chapter_index'], language=language)]
        for other in matches)]
    groups = []
    group_end = -1
    for item in sorted(matches, key=lambda item: (item['first'], item['last'], item['chapter_id'], item['line_index'])):
        if not groups or item['first'] >= group_end:
            groups.append([])
            group_end = item['last']
        groups[-1].append(item)
        group_end = max(group_end, item['last'])
    intervals, unresolved, assigned, covered, counts = [], [], set(), set(), {}
    for group in groups:
        first = min(item['first'] for item in group)
        last = max(item['last'] for item in group)
        covered.update(range(first, last))
        owners = {(item['chapter_id'], item['line_index']) for item in group}
        before = first > 0 and stream[first][1] - stream[first - 1][2] < MIN_BOUNDARY_PAUSE
        after = last < len(stream) and stream[last][1] - stream[last - 1][2] < MIN_BOUNDARY_PAUSE
        reason = ('ambiguous_candidates' if len(owners) > 1 else
                  'adjacent_speech' if before or after else None)
        options = [{key: value for key, value in item.items() if key not in {'first', 'last'}} for item in group]
        common = {'start': stream[first][1], 'end': stream[last - 1][2]}
        if reason:
            unresolved.append({**common, 'text': ' '.join(words[first:last]), 'reason': reason, 'candidates': options})
            continue
        line = options[0]
        key = (line['chapter_id'], line['line_index'])
        counts[key] = counts.get(key, 0) + 1
        intervals.append({**line, **common, 'score': 1.0, 'take_index': counts[key]})
        assigned.add(key)
    cursor = 0
    while cursor < len(stream):
        if cursor in covered:
            cursor += 1
            continue
        end = cursor + 1
        while end < len(stream) and end not in covered:
            end += 1
        unresolved.append({'start': stream[cursor][1], 'end': stream[end - 1][2],
            'text': ' '.join(words[cursor:end]), 'reason': 'unmatched_speech', 'candidates': []})
        cursor = end
    return {'intervals': intervals, 'unresolved': sorted(unresolved, key=lambda item: (item['start'], item['end'])),
            'missing': [line for line in expected if (line['chapter_id'], line['line_index']) not in assigned]}
