"""Блоки глав в записи роли целиком и резка по паузам (`app/services/take_split.py`)."""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import pytest

from app.services.take_split import (
    ChapterBlock,
    OutOfOrder,
    chapter_blocks,
    counts,
    cut_audio,
    plan_cuts,
    slice_transcript,
)

FIXTURE = Path(__file__).parent / "fixtures" / "asr" / "role_six_chapters.json"


def _segments(lines: list[str], *, start: float = 0.0, gap: float = 1.0) -> tuple[list[dict], float]:
    """Сегменты распознавания: каждая реплика — отдельный сегмент, слово по 0,4 с."""
    segments: list[dict] = []
    clock = start
    for line in lines:
        words = []
        for word in line.split():
            words.append({"word": word, "start": round(clock, 3), "end": round(clock + 0.3, 3)})
            clock += 0.4
        segments.append({
            "start": words[0]["start"], "end": words[-1]["end"],
            "text": line, "words": words,
        })
        clock += gap
    return segments, clock


CH55 = [
    "Сегодня мы отправляемся в далёкий путь через горы",
    "Возьми с собой тёплый плащ и немного хлеба",
    "Дорога будет долгой и очень опасной для нас",
    "Я не боюсь ни холода ни голодных волков",
]
CH57 = [
    "Да.",
    "Колдун ушёл за перевал ещё до рассвета",
    "Мы догоним его к вечеру если поторопимся",
    "Тогда седлайте коней и не теряйте времени",
    "Ворота замка уже закрыли за нами навсегда",
    "Никто не вернётся отсюда прежним человеком",
]


def test_six_chapters_split_at_pauses():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    chapters = [
        (int(key), f"ch{key}", f"Глава {key}", lines)
        for key, lines in sorted(data["chapters"].items(), key=lambda item: int(item[0]))
    ]
    blocks = chapter_blocks(chapters, data["segments"])
    cuts = plan_cuts(blocks, 434.31)
    assert [block.chapter_index for block, _, _ in cuts] == [1, 12, 14, 34, 47, 59]
    assert cuts[0][1] == 0
    assert cuts[-1][2] == 434.31
    bounds = [end for _, _, end in cuts[:-1]]
    for got, want in zip(bounds, [22.2, 62.0, 167.0, 184.3, 396.4]):
        assert abs(got - want) <= 1.0, (bounds, got, want)
    # Каждый следующий кусок начинается там, где кончился предыдущий.
    for (_, _, end), (_, start, _) in zip(cuts, cuts[1:]):
        assert start == end
    # Выброс главы 59 на 15-й секунде не утащил начало её блока в начало файла.
    block59 = next(block for block in blocks if block.chapter_index == 59)
    assert block59.start > 390
    assert block59.label == "Глава 59"


def test_one_stray_short_line_does_not_count_a_chapter():
    segments, duration = _segments(CH55 + [CH57[0]])
    blocks = chapter_blocks(
        [(55, "c55", "Глава 55", CH55), (57, "c57", "Глава 57", CH57)], segments,
    )
    block57 = next(block for block in blocks if block.chapter_index == 57)
    assert block57.found <= 1
    assert not counts(block57)
    cuts = plan_cuts(blocks, duration)
    assert [(block.chapter_index, start, end) for block, start, end in cuts] == [(55, 0, duration)]


def test_counts_threshold():
    def block(found: int, total: int) -> ChapterBlock:
        return ChapterBlock(1, "c", "Глава 1", 0.0, 1.0, found, total)

    assert counts(block(3, 9))
    assert not counts(block(2, 9))
    assert counts(block(2, 2))
    assert not counts(block(1, 2))
    assert counts(block(1, 1))
    assert not counts(block(0, 0))


def test_named_single_chapter_is_not_cut():
    segments, duration = _segments(CH55 + [CH57[1]])
    blocks = chapter_blocks(
        [(55, "c55", "Глава 55", CH55), (57, "c57", "Глава 57", CH57)], segments,
    )
    cuts = plan_cuts(blocks, duration, filename_chapter=55)
    assert len(cuts) == 1
    block, start, end = cuts[0]
    assert (block.chapter_index, start, end) == (55, 0, duration)


def test_fix_is_never_cut():
    segments, duration = _segments(CH55 + CH57[1:])
    blocks = chapter_blocks(
        [(55, "c55", "Глава 55", CH55), (57, "c57", "Глава 57", CH57)], segments,
    )
    assert len(plan_cuts(blocks, duration)) == 2  # без фикса это колбаса из двух глав

    named = plan_cuts(blocks, duration, filename_chapter=57, is_fix=True)
    assert [(block.chapter_index, start, end) for block, start, end in named] == [(57, 0, duration)]

    guessed = plan_cuts(blocks, duration, is_fix=True)
    assert [(block.chapter_index, start, end) for block, start, end in guessed] == [(57, 0, duration)]


def test_out_of_order_raises():
    segments, duration = _segments(CH57[1:] + CH55)
    blocks = chapter_blocks(
        [(55, "c55", "Глава 55", CH55), (57, "c57", "Глава 57", CH57)], segments,
    )
    with pytest.raises(OutOfOrder):
        plan_cuts(blocks, duration)


def test_nothing_counted_returns_empty():
    segments, duration = _segments(["Совсем другой текст о погоде и рыбалке на озере"])
    blocks = chapter_blocks(
        [(55, "c55", "Глава 55", CH55), (57, "c57", "Глава 57", CH57)], segments,
    )
    assert plan_cuts(blocks, duration) == []
    assert plan_cuts(blocks, duration, is_fix=True) == []


def test_slice_shifts_time_to_zero():
    segments = [
        {"start": 0.0, "end": 2.0, "text": "раз два",
         "words": [{"word": "раз", "start": 0.0, "end": 0.8}, {"word": "два", "start": 1.0, "end": 2.0}]},
        {"start": 10.0, "end": 12.5, "text": "три четыре",
         "words": [{"word": "три", "start": 10.0, "end": 11.0}, {"word": "четыре", "start": 11.5, "end": 12.5}]},
        {"start": 20.0, "end": 21.0, "text": "пять",
         "words": [{"word": "пять", "start": 20.0, "end": 21.0}]},
    ]
    piece = slice_transcript(segments, 1.5, 15.0)
    assert piece["text"] == "три четыре"
    assert piece["segments"] == [{
        "start": 8.5, "end": 11.0, "text": "три четыре",
        "words": [{"word": "три", "start": 8.5, "end": 9.5}, {"word": "четыре", "start": 10.0, "end": 11.0}],
    }]


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def test_cut_audio_keeps_samples(tmp_path):
    src = tmp_path / "whole.wav"
    rate = 16000
    with wave.open(str(src), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x00" * rate * 3)
    out = tmp_path / "piece.wav"
    cut_audio(str(src), 1.0, 2.0, str(out))
    assert abs(_probe_duration(out) - 1.0) <= 0.01


# --- Ревью 9989ea2, раунд 1: чужие короткие совпадения не двигают границы ---------

CH12 = [
    "Старый мельник давно ждал гостей из города",
    "Он вынес на крыльцо самовар и пряники",
    "Солнце садилось за рекой и становилось прохладно",
]
CH14 = [
    "Утром в деревню пришёл незнакомый всадник",
    "Нет, не пойду я с вами никуда сегодня",
    "Всадник пожал плечами и повернул коня",
    "Да, конечно, ты прав, мне пора уходить",
    "Дети бежали за ним до самого колодца",
    "Я не знаю, что сказать тебе на это",
    "Собаки лаяли до тех пор пока не стемнело",
    "Да.",
    "Ночью над лесом поднялась огромная луна",
]
CHAPTER_PAUSE = 6.0


def _two_chapters(first: list[str], second: list[str]) -> tuple[list[dict], float, float]:
    """Гл. 12 и гл. 14 подряд с паузой между ними; середина паузы — ожидаемая граница."""
    head, clock = _segments(first)
    head_end = head[-1]["end"]
    tail, duration = _segments(second, start=clock + CHAPTER_PAUSE)
    return head + tail, duration, (head_end + tail[0]["start"]) / 2


def _cut_shape(cuts):
    return [(block.chapter_index, round(start, 2), round(end, 2)) for block, start, end in cuts]


def test_phantom_one_line_chapter_inside_next_does_not_steal_it():
    ch12 = CH12 + ["Пора спать, завтра рано вставать"]
    segments, duration, boundary = _two_chapters(ch12, CH14)
    chapters = [
        (12, "c12", "Глава 12", ch12),
        (13, "c13", "Глава 13", ["Нет, не пойду."]),
        (14, "c14", "Глава 14", CH14),
    ]
    blocks = chapter_blocks(chapters, segments)
    block13 = next(block for block in blocks if block.chapter_index == 13)
    block14 = next(block for block in blocks if block.chapter_index == 14)
    assert not counts(block13)
    first_of_14 = next(segment for segment in segments if segment["text"] == CH14[0])
    assert block14.start == first_of_14["start"]
    assert _cut_shape(plan_cuts(blocks, duration)) == [
        (12, 0, round(boundary, 2)), (14, round(boundary, 2), round(duration, 2)),
    ]


@pytest.mark.parametrize(("last_line", "heard_as"), [
    ("Да.", None),               # B: «Да.» не распознано, в гл. 14 есть «Да, конечно…»
    ("Да.", "Ага."),             # C: «Да.» услышано как «Ага.», в гл. 14 есть «Да.»
    ("Я не знаю.", "Не помню."),  # D: три слова, в гл. 14 есть «Я не знаю, что…»
])
def test_stray_late_match_of_last_line_keeps_order(last_line, heard_as):
    script12 = CH12 + [last_line]
    spoken12 = CH12 + ([heard_as] if heard_as else [])
    segments, duration, boundary = _two_chapters(spoken12, CH14)
    blocks = chapter_blocks(
        [(12, "c12", "Глава 12", script12), (14, "c14", "Глава 14", CH14)], segments,
    )
    block12 = next(block for block in blocks if block.chapter_index == 12)
    assert block12.found == len(CH12)  # последняя реплика — пропуск, а не чужое место
    cuts = plan_cuts(blocks, duration)
    assert [(block.chapter_index, start) for block, start, _ in cuts] == [(12, 0), (14, cuts[0][2])]
    assert cuts[-1][2] == duration
    # Граница — в настоящей паузе между главами: после всего сказанного в гл. 12
    # (и неузнанного «Ага.» тоже), до первого слова гл. 14.
    last_of_12 = max(segment["end"] for segment in segments if segment["start"] < boundary)
    first_of_14 = min(segment["start"] for segment in segments if segment["start"] > boundary)
    assert last_of_12 < cuts[0][2] < first_of_14


def test_chapter_read_before_earlier_one_raises():
    segments, duration, _ = _two_chapters(CH14, CH12)
    blocks = chapter_blocks(
        [(12, "c12", "Глава 12", CH12), (14, "c14", "Глава 14", CH14)], segments,
    )
    with pytest.raises(OutOfOrder):
        plan_cuts(blocks, duration)


def test_fix_without_name_takes_most_found_without_threshold():
    # Фикс: две реплики главы 57 из шести — порог трети не пройден, но это фикс.
    segments, duration = _segments(CH57[1:3])
    blocks = chapter_blocks(
        [(55, "c55", "Глава 55", CH55), (57, "c57", "Глава 57", CH57 + CH12)], segments,
    )
    block57 = next(block for block in blocks if block.chapter_index == 57)
    assert not counts(block57)
    assert _cut_shape(plan_cuts(blocks, duration, is_fix=True)) == [(57, 0, round(duration, 2))]


def test_fix_without_name_tie_goes_to_human():
    blocks = [
        ChapterBlock(55, "c55", "Глава 55", 0.0, 1.0, 2, 10),
        ChapterBlock(57, "c57", "Глава 57", 2.0, 3.0, 2, 10),
    ]
    assert plan_cuts(blocks, 4.0, is_fix=True) == []
    assert plan_cuts([ChapterBlock(55, "c55", "Глава 55", 0.0, 0.0, 0, 10)], 4.0, is_fix=True) == []


def test_fix_with_name_takes_named_chapter():
    blocks = [
        ChapterBlock(55, "c55", "Глава 55", 0.0, 1.0, 9, 10),
        ChapterBlock(57, "c57", "Глава 57", 2.0, 3.0, 1, 10),
    ]
    assert _cut_shape(plan_cuts(blocks, 4.0, filename_chapter=57, is_fix=True)) == [(57, 0, 4.0)]


def test_name_mismatch_reported_for_single_piece():
    from app.services.take_split import name_mismatch

    segments, duration = _segments(CH55)
    blocks = chapter_blocks(
        [(55, "c55", "Глава 55", CH55), (57, "c57", "Глава 57", CH57)], segments,
    )
    cuts = plan_cuts(blocks, duration, filename_chapter=57)
    assert _cut_shape(cuts) == [(55, 0, round(duration, 2))]
    assert name_mismatch(cuts, 57) == 57
    assert name_mismatch(cuts, 55) is None
    assert name_mismatch(cuts, 0) is None
    assert name_mismatch([], 57) is None


def test_cut_flac_reports_real_duration(tmp_path):
    wav = tmp_path / "whole.wav"
    rate = 16000
    with wave.open(str(wav), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x01\x00" * rate * 10)
    src = tmp_path / "whole.flac"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), str(src)], check=True)
    out = tmp_path / "piece.flac"
    cut_audio(str(src), 3.0, 5.0, str(out))
    assert abs(_probe_duration(out) - 2.0) <= 0.01


# --- Ревью be9bd16, раунд 2: фальстарт длинной реплики — не глава ------------------

CH14_NO = [CH14[1], CH14[0], *CH14[2:]]  # гл. 14 открывается «Нет, не пойду я с вами…»


def _chapters_12_13_14(ch13: list[str]) -> list[tuple[int, str, str, list[str]]]:
    return [
        (12, "c12", "Глава 12", CH12),
        (13, "c13", "Глава 13", ch13),
        (14, "c14", "Глава 14", CH14_NO),
    ]


@pytest.mark.parametrize("times", [1, 2])
def test_false_start_of_next_chapter_is_not_a_phantom_chapter(times):
    # P2b (каждая реплика один раз) и P10 (каждая дважды, как у Белкиной): диктор
    # читает гл. 12, спотыкается «Нет, не пойду…» и читает гл. 14 с начала. Гл. 13 не
    # читалась вовсе.
    def twice(lines):
        return [line for line in lines for _ in range(times)]

    head, clock = _segments(twice(CH12))
    tail, duration = _segments(["Нет, не пойду…", *twice(CH14_NO)], start=clock + CHAPTER_PAUSE)
    segments = head + tail
    blocks = chapter_blocks(_chapters_12_13_14(["Нет, не пойду."]), segments)
    block13 = next(block for block in blocks if block.chapter_index == 13)
    assert not counts(block13)
    cuts = plan_cuts(blocks, duration)
    assert [block.chapter_index for block, _, _ in cuts] == [12, 14]
    # Фальстарт остаётся в куске гл. 14.
    assert cuts[1][1] < tail[0]["start"]


@pytest.mark.parametrize(("line", "times"), [
    ("Да.", 1), ("Нет, не пойду.", 1), ("Нет, не пойду.", 2), ("Нет, не пойду.", 3),
])
def test_real_one_line_chapter_set_off_by_pauses_counts(line, times):
    head, clock = _segments(CH12)
    middle, clock = _segments([line] * times, start=clock + CHAPTER_PAUSE)
    tail, duration = _segments(CH14_NO, start=clock + CHAPTER_PAUSE)
    blocks = chapter_blocks(_chapters_12_13_14([line]), head + middle + tail)
    cuts = plan_cuts(blocks, duration)
    assert [block.chapter_index for block, _, _ in cuts] == [12, 13, 14]
    assert cuts[0][2] < middle[0]["start"] and middle[-1]["end"] < cuts[1][2] < tail[0]["start"]
