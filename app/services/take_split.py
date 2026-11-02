"""Запись роли целиком («колбаса»): какие главы в ней есть и где резать.

Диктор присылает в бот один файл на несколько глав. Чтобы дубли легли по главам, как
с сайта, надо найти в распознанном блок каждой главы и разрезать файл в паузах между
блоками. 28.10 так руками разрезали «хубрху» и «юмкубу»; этот модуль — тот же приём,
чтобы бот и будущий раздел сайта резали одинаково.

Здесь только логика и ffmpeg, без базы: главы вызывающий передаёт кортежами
(`role_chapters` + `expected_lines`), распознавание — сегментами со словами.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass

from app.services.asr_align import align_transcript, normalize_words


@dataclass
class ChapterBlock:
    chapter_index: int
    chapter_id: str
    #: chapter_title — метка, под которой дубль привязан к главе
    label: str
    #: начало первого найденного дубля реплики
    start: float
    #: конец последнего
    end: float
    found: int
    total: int


class OutOfOrder(Exception):
    """Главы в файле перекрываются или идут не по возрастанию номеров.

    Такой файл не режут: молча потерять главу хуже, чем отдать файл человеку.
    """


def counts(block: ChapterBlock) -> bool:
    """Нашлась ли в файле заметная часть главы.

    Одна случайная короткая реплика («Да.» звучит в любой главе) не делает главу
    присланной — иначе каждая колбаса резалась бы на лишние куски. Порог — треть
    реплик; глава из одной-двух реплик должна найтись вся, потому что треть от двух —
    это та самая одна случайная.
    """
    if block.total <= 0:
        return False
    if block.total <= 2:
        return block.found == block.total
    return block.found * 3 >= block.total


@dataclass
class _Take:
    """Один найденный подход реплики: чей он и сколько в нём слов."""
    #: место главы в переданном списке — он по возрастанию номера, то есть в порядке чтения
    chapter: int
    line: int
    start: float
    end: float
    #: слов в реплике сценария — вес подхода: чем длиннее совпадение, тем меньше оно
    #: похоже на случайное
    words: int
    #: слова реплики сценария — по ним узнаётся фальстарт чужой длинной реплики
    script: tuple[str, ...] = ()


#: дольше этой паузы между репликами — уже не запинка с повтором, а граница. Внутри
#: главы реплики идут через секунду; между главами у «юмкуби» 2,7–6,7 с.
FALSE_START_PAUSE = 1.5


def _drop_false_starts(takes: list[_Take], totals: list[int]) -> list[_Take]:
    """Выкинуть подход короткой главы, который на деле — фальстарт чужой реплики.

    Диктор часто спотыкается и начинает реплику заново: «Нет, не пойду…» — и сразу
    «Нет, не пойду я с вами никуда сегодня». Однострочная гл. 13 «Нет, не пойду.»
    находит себя в этой запинке, засчитывается целиком и отрезает кусок у гл. 14.
    Признак фальстарта: сразу за подходом, без паузы длиннее `FALSE_START_PAUSE`, идёт
    подход другой главы, чья реплика начинается теми же словами. Только для глав из
    одной-двух реплик: длинной главе одна выкинутая реплика ничего не решает, а
    короткую одна запинка делает «присланной». Настоящая однострочная глава отделена
    от соседки паузой между главами и под правило не попадает.
    """
    ordered = sorted(takes, key=lambda take: (take.start, take.chapter, take.line))
    dropped: set[int] = set()
    for i, take in enumerate(ordered):
        if totals[take.chapter] > 2 or not take.script:
            continue
        for later in ordered[i + 1:]:
            if later.start - take.end > FALSE_START_PAUSE:
                break
            if (
                later.chapter != take.chapter
                and len(later.script) > len(take.script)
                and later.script[:len(take.script)] == take.script
            ):
                dropped.add(i)
                break
    return [take for i, take in enumerate(ordered) if i not in dropped]


def _drop_overlapped(takes: list[_Take]) -> list[_Take]:
    """Отрезок записи, который «слышат» две главы, отдать более длинной реплике.

    28.10 на пробе: однострочная гл. 13 «Нет, не пойду.» нашлась внутри реплики гл. 14
    «Нет, не пойду я с вами никуда сегодня» — то же место записи. Длинная реплика
    случайно не совпадает, короткая — сплошь и рядом. При равной длине не решаем
    здесь: дальше это сделает порядок чтения.
    """
    ordered = sorted(takes, key=lambda take: (take.start, take.chapter, take.line))
    dropped: set[int] = set()
    for i, take in enumerate(ordered):
        for j in range(i + 1, len(ordered)):
            other = ordered[j]
            if other.start >= take.end:
                break
            if other.chapter == take.chapter or other.words == take.words:
                continue
            dropped.add(i if take.words < other.words else j)
    return [take for i, take in enumerate(ordered) if i not in dropped]


def _reading_order(takes: list[_Take]) -> set[int]:
    """Номера подходов, которые укладываются в чтение глав по возрастанию.

    Диктор читает главы по порядку, поэтому по времени номера глав подходов не
    убывают. Короткая последняя реплика главы, совпавшая где-то в следующей главе,
    этот порядок ломает; если её не выкинуть, блок главы растянется на соседку и
    вся колбаса станет «не по порядку». Берём самую тяжёлую (по словам) неубывающую
    цепочку — она детерминирована и за O(подходы × главы); всё, что в неё не вошло, —
    случайные совпадения. `takes` отсортированы по времени.
    """
    best: dict[int, tuple[int, int]] = {}  # глава -> (вес лучшей цепочки, её последний подход)
    score = [0] * len(takes)
    previous = [-1] * len(takes)
    for i, take in enumerate(takes):
        before = max(
            ((weight, chapter, last) for chapter, (weight, last) in best.items() if chapter <= take.chapter),
            default=None,
        )
        score[i] = take.words + (before[0] if before else 0)
        previous[i] = before[2] if before else -1
        if take.chapter not in best or score[i] >= best[take.chapter][0]:
            best[take.chapter] = (score[i], i)
    if not takes:
        return set()
    tail = max(range(len(takes)), key=lambda i: (score[i], i))
    chain: set[int] = set()
    while tail >= 0:
        chain.add(tail)
        tail = previous[tail]
    return chain


def chapter_blocks(
    chapters: list[tuple[int, str, str, list[str]]], segments: list[dict],
) -> list[ChapterBlock]:
    """Блок каждой главы роли в распознанном файле.

    `chapters` — (номер, id главы, метка, реплики роли) по возрастанию номера. Блок
    возвращается для каждой главы, засчитана она или нет: решать, что резать, —
    дело `plan_cuts`, а отчёт диктору хочет знать и про ненайденные.

    Каждая глава сверяется со всей записью, а потом подходы всех глав чистятся от
    случайных совпадений: сначала место, которое слышат две главы, отходит более
    длинной реплике (`_drop_overlapped`), запинка перед чужой длинной репликой не
    засчитывает короткую главу (`_drop_false_starts`), затем выкидывается всё, что ломает чтение
    глав по возрастанию (`_reading_order`). Раньше выбросом считалась находка раньше
    конца предыдущей засчитанной главы — и однострочная «глава-призрак» внутри
    следующей сама становилась засчитанной и отрезала у соседки начало.
    """
    totals: list[int] = []
    takes: list[_Take] = []
    for position, (_, _, _, expected) in enumerate(chapters):
        alignment = align_transcript(list(expected or []), segments)
        totals.append(len(alignment["lines"]))
        for line in alignment["lines"]:
            script = tuple(normalize_words(line.get("text") or ""))
            for take in line.get("takes") or []:
                takes.append(_Take(
                    position, line["index"], take["start"], take["end"], len(script), script,
                ))

    takes = _drop_false_starts(_drop_overlapped(takes), totals)
    chain = _reading_order(takes)
    kept: dict[int, list[_Take]] = {}
    early: dict[int, list[_Take]] = {}
    for i, take in enumerate(takes):
        (kept if i in chain else early).setdefault(take.chapter, []).append(take)

    # Выкинутое порядком — не всегда выброс. Если его столько, что оно само засчитало
    # бы главу, и лежит оно не внутри чужой главы, — это глава, прочитанная не по
    # порядку. Её возвращаем: блок ляжет внахлёст или не по возрастанию, и `plan_cuts`
    # откажется резать, вместо того чтобы молча потерять главу.
    spans = {
        chapter: (min(take.start for take in group), max(take.end for take in group))
        for chapter, group in kept.items()
    }
    for chapter, group in early.items():
        lines = {take.line for take in group}
        if not counts(ChapterBlock(0, "", "", 0.0, 0.0, len(lines), totals[chapter])):
            continue
        inside = all(
            any(
                other != chapter and low <= take.start and take.end <= high
                for other, (low, high) in spans.items()
            )
            for take in group
        )
        if not inside:
            kept.setdefault(chapter, []).extend(group)

    blocks: list[ChapterBlock] = []
    for position, (index, chapter_id, label, _) in enumerate(chapters):
        group = kept.get(position, [])
        blocks.append(ChapterBlock(
            chapter_index=index,
            chapter_id=chapter_id,
            label=label,
            start=min((take.start for take in group), default=0.0),
            end=max((take.end for take in group), default=0.0),
            found=len({take.line for take in group}),
            total=totals[position],
        ))
    return blocks


def plan_cuts(
    blocks: list[ChapterBlock], duration: float, *, filename_chapter: int = 0,
    is_fix: bool = False,
) -> list[tuple[ChapterBlock, float, float]]:
    """Куски файла: (глава, начало, конец). Пустой список — разложить нечего.

    Фикс (`is_fix`) не режется: глава из имени (`filename_chapter`), а без неё — та,
    где нашлось больше всего реплик (хотя бы одна, порог трети не действует); при
    ничьей — `[]`, файл разбирает человек. Глава в имени, расходящаяся с единственной
    засчитанной, уступает записи — расхождение отдаёт `name_mismatch`.
    """
    counted = [block for block in blocks if counts(block)]
    if is_fix:
        # Фикс — пересъёмка нескольких реплик, его не режут никогда.
        named = next(
            (block for block in blocks if filename_chapter and block.chapter_index == filename_chapter),
            None,
        )
        if named is not None:
            return [(named, 0, duration)]
        # Без главы в имени — глава, где нашлось больше всего реплик, и без порога
        # трети: в фиксе всего несколько реплик, порог он не пройдёт никогда. Ничья —
        # не угадываем: пустой результат, файл уйдёт человеку в «не разобрано».
        most = max((block.found for block in blocks), default=0)
        leaders = [block for block in blocks if block.found == most]
        if most >= 1 and len(leaders) == 1:
            return [(leaders[0], 0, duration)]
        return []
    if not counted:
        return []
    if len(counted) == 1:
        # Одна глава — файл целиком, без резки. Сюда же попадает «глава в имени и
        # других засчитанных нет»: лишняя короткая реплика соседней главы засчитанной
        # не становится и файл не режет.
        return [(counted[0], 0, duration)]

    ordered = sorted(counted, key=lambda block: block.chapter_index)
    for before, after in zip(ordered, ordered[1:]):
        if after.start < before.end:
            raise OutOfOrder(
                f"глава {after.chapter_index} начинается ({after.start:.2f} с) раньше, "
                f"чем кончается глава {before.chapter_index} ({before.end:.2f} с)"
            )
    # Граница — середина паузы между главами: так ни один кусок не начинается и не
    # кончается вплотную к слову, и обрезанного вдоха не слышно ни с одной стороны.
    bounds = [round((before.end + after.start) / 2, 3) for before, after in zip(ordered, ordered[1:])]
    starts = [0, *bounds]
    ends = [*bounds, duration]
    return list(zip(ordered, starts, ends))


def name_mismatch(
    cuts: list[tuple[ChapterBlock, float, float]], filename_chapter: int,
) -> int | None:
    """Глава из имени файла, если файл целиком лёг в другую главу.

    Запись главнее имени: реплики не врут, а имя диктор мог скопировать от прошлого
    файла. Но молча переложить нельзя — владелец должен увидеть расхождение, вдруг
    ошиблась как раз сверка. Колбасу (несколько кусков) не трогаем: там имя заведомо
    не про весь файл.
    """
    if not filename_chapter or len(cuts) != 1:
        return None
    return filename_chapter if cuts[0][0].chapter_index != filename_chapter else None


def slice_transcript(segments: list[dict], start: float, end: float) -> dict:
    """Распознанное куска [start, end) со временем от начала куска.

    Кусок сверяется тем же `run_asr_for_take`, что и дубль с сайта, но без второй
    оплаты: ему подают срез уже распознанного. Слово принадлежит куску по своему
    началу — граница режет паузу, а не слово, так что спорных слов нет.
    """
    pieces: list[dict] = []
    for segment in segments:
        words = [
            {
                "word": word["word"],
                "start": round(max(0.0, word["start"] - start), 3),
                "end": round(min(end, word["end"]) - start, 3),
            }
            for word in segment.get("words") or []
            if start <= word["start"] < end
        ]
        if not words:
            continue
        pieces.append({
            "start": words[0]["start"],
            "end": words[-1]["end"],
            "text": " ".join(word["word"] for word in words),
            "words": words,
        })
    return {"text": " ".join(piece["text"] for piece in pieces), "segments": pieces}


#: сжатие без потерь, которое режется пересжатием в самого себя, а не копией потока.
#: FLAC при `-c copy` сохраняет в STREAMINFO длительность целого файла: кусок в 2 с
#: показывался как 10 с, и сессия Audition растянула бы дорожку на пустоту.
LOSSLESS_CODECS = frozenset({"flac", "alac", "wavpack"})


def _audio_codec(src: str) -> str:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_name", "-of", "csv=p=0", src,
        ],
        check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def cut_audio(src: str, start: float, end: float, out: str) -> None:
    """Вырезать [start, end) без перекодирования.

    Копия потока, а не пересжатие: дубль диктора уходит в сессию Audition, и каждое
    лишнее сжатие ухудшает запись, которую потом сводят ухом. Исключение — PCM (WAV):
    `-c copy` режет его пакетами по 1024 отсчёта, и кусок выходит длиннее на десятки
    миллисекунд — хвост соседней главы; FLAC и прочее сжатие без потерь при копии
    хранят длительность целого файла (`LOSSLESS_CODECS`). Их пересжимаем в тот же
    кодек: без потерь это отсчёт в отсчёт, точный срез, а не ухудшение.
    """
    codec = _audio_codec(src)
    lossless = codec.startswith("pcm_") or codec in LOSSLESS_CODECS
    audio = ["-c:a", codec] if lossless else ["-c", "copy"]
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{start:.3f}", "-i", src,
            "-t", f"{max(0.0, end - start):.3f}",
            *audio, out,
        ],
        check=True,
        capture_output=True,
    )


#: длина одной части при распознавании. Azure fast transcription принимает запись
#: до ~2 ч (и до `asr_azure.MAX_UPLOAD_BYTES`); точный предел в документации менялся,
#: поэтому берём час с двукратным запасом: часть не упрётся ни в длительность, ни в
#: размер mp3, а лишняя граница стоит дёшево.
PART_SECONDS = 3600

#: окно поиска тишины вокруг отметки и параметры silencedetect: граница в паузе, а не
#: посреди слова — иначе слово на стыке распознается дважды или совсем никак
SILENCE_WINDOW = 30.0
_SILENCE_ARGS = "silencedetect=noise=-35dB:d=0.4"


def _silence_near(src: str, mark: float, duration: float) -> float:
    """Середина ближайшей к отметке тишины в ±SILENCE_WINDOW, иначе сама отметка."""
    lo = max(0.0, mark - SILENCE_WINDOW)
    hi = min(duration, mark + SILENCE_WINDOW)
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-ss", f"{lo:.3f}", "-to", f"{hi:.3f}", "-i", src,
         "-af", _SILENCE_ARGS, "-f", "null", "-"],
        capture_output=True, text=True,
    )
    # с -ss до -i времена в логе считаются от начала окна
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", proc.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", proc.stderr)]
    best = None
    for i, a in enumerate(starts):
        # тишина, не закончившаяся до конца окна, кончается вместе с ним
        b = ends[i] if i < len(ends) else hi - lo
        mid = lo + (max(a, 0.0) + b) / 2
        if best is None or abs(mid - mark) < abs(best - mark):
            best = mid
    return round(best, 3) if best is not None else mark


def _merge(parts: list[tuple[float, dict]]) -> dict:
    segments: list[dict] = []
    texts: list[str] = []
    for offset, result in parts:
        if result.get("text"):
            texts.append(result["text"])
        for seg in result.get("segments") or []:
            segments.append({
                **seg,
                "start": round(seg["start"] + offset, 3),
                "end": round(seg["end"] + offset, 3),
                "words": [
                    {**w, "start": round(w["start"] + offset, 3), "end": round(w["end"] + offset, 3)}
                    for w in seg.get("words") or []
                ],
            })
    return {"text": " ".join(texts), "segments": segments}


def transcribe_whole(path: str, *, transcribe_part, workdir: str, duration: float) -> dict:
    """Распознать запись любой длины, разрезав её на части не длиннее PART_SECONDS.

    Каждая часть — отдельный платный запрос, а длинная запись может упасть на третьем
    часу. Поэтому результат каждой части сразу пишется в `workdir/part-<n>.json` вместе
    с границами, и повторный вызов платит только за недостающие. Сохранённое берётся,
    лишь если границы те же: иначе сдвиг времени склеил бы чужой кусок.
    """
    if duration < PART_SECONDS:
        return transcribe_part(path)

    marks = []
    t = float(PART_SECONDS)
    while t < duration:
        marks.append(t)
        t += PART_SECONDS
    cuts = [0.0] + [_silence_near(path, m, duration) for m in marks] + [float(duration)]

    os.makedirs(workdir, exist_ok=True)
    ext = os.path.splitext(path)[1] or ".wav"
    parts: list[tuple[float, dict]] = []
    for n, (start, end) in enumerate(zip(cuts, cuts[1:])):
        saved = os.path.join(workdir, f"part-{n}.json")
        result = None
        if os.path.exists(saved):
            try:
                with open(saved, encoding="utf-8") as f:
                    data = json.load(f)
                if abs(data["start"] - start) < 0.01 and abs(data["end"] - end) < 0.01:
                    result = data["result"]
            except (OSError, ValueError, KeyError):
                result = None
        if result is None:
            piece = os.path.join(workdir, f"part-{n}{ext}")
            cut_audio(path, start, end, piece)
            result = transcribe_part(piece)
            tmp = saved + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"start": start, "end": end, "result": result}, f, ensure_ascii=False)
            os.replace(tmp, saved)
        parts.append((start, result))
    return _merge(parts)
