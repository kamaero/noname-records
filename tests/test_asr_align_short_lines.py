"""Сверка не должна терять короткие реплики и возгласы в длинной записи."""
from __future__ import annotations

import json
from pathlib import Path

from app.services.asr_align import align_transcript, word_stream

FIXTURES = Path(__file__).parent / "fixtures" / "asr"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_short_answers_survive_unclear_words():
    data = _load("short_lines_43.json")
    result = align_transcript(data["expected"], data["segments"])
    found = {line["text"] for line in result["lines"] if line["matched"]}
    for text in ("Монета!", "Подсказка!", "Неизвестно.", "Хорошо, вы угадали,"):
        assert text in found
    assert result["matched"] == 41
    assert all(not line["matched"] for line in result["lines"][-2:])
    assert len(result["lines"][8]["takes"]) == 2


def test_short_shouts_are_found():
    data = _load("short_lines_21.json")
    result = align_transcript(data["expected"], data["segments"])
    found = {line["text"] for line in result["lines"] if line["matched"]}
    assert {"Тогда запиши!", "Ты ошибся,", "А где ещё?!"} <= found
    assert result["matched"] == 19
    assert all(not line["matched"] for line in result["lines"][-2:])
    assert len(result["lines"][8]["takes"]) == 2


def test_rereading_a_pair_keeps_takes_of_both_lines():
    """Диктор перечитывает пару реплик: «А», «Б», «А», «Б». Подход «А» перепрыгивает «Б»,
    но «Б» звучит и за ним — это повтор пары, и подходы у «А» остаются (замер по проду:
    192 таких подхода снимались первой версией правила об юмкубе)."""
    first = "Ты давно видела наш старый фонарь?"
    second = "А если пойдёт дождь, где найдём укрытие?"
    said = " ".join([first, second, first, second])
    result = align_transcript([first, second], [{"text": said, "start": 0.0, "end": 20.0}])
    assert [line["matched"] for line in result["lines"]] == [True, True]
    assert len(result["lines"][0]["takes"]) == 2


def _heard(text: str) -> list[dict]:
    return [{"text": text, "start": 0.0, "end": 10.0}]


def _assert_skipped_short_line(expected: list[str], heard: str) -> None:
    """Пропущенная «Нет,» не находится нигде, прочитанные — все, и первый подход
    соседки начинается с её первого слова, а не с середины."""
    result = align_transcript(expected, _heard(heard))
    assert [line["matched"] for line in result["lines"]] == [False] + [True] * (len(expected) - 1)
    stream = word_stream(_heard(heard))
    assert result["lines"][1]["start"] == stream[0][1]  # «сам»


def test_skipped_short_line_is_not_found_inside_the_neighbour():
    """Диктор пропустил «Нет,». Прыжок к «не» из «Не трогай…» отвергнут, и реплика не
    должна «найтись» в «нести» — внутри соседки, которую диктор прочитал: иначе соседка
    уходит в пропуски (беда «Эины» 20.10)."""
    _assert_skipped_short_line(
        ["Нет,", "Сам нести буду.", "Не трогай меня, слышишь."],
        "сам нести буду не трогай меня слышишь",
    )


def test_skipped_short_line_with_a_line_in_between():
    """То же, но между соседкой и местом прыжка ещё реплика: соседка, из-за которой
    прыжок отвергнут, не обязана быть той, что звучит после нового места."""
    _assert_skipped_short_line(
        ["Нет,", "Сам нести буду.", "Иди отсюда прочь скорее.", "Не трогай меня, слышишь."],
        "сам нести буду иди отсюда прочь скорее не трогай меня слышишь",
    )


def test_skipped_short_line_does_not_clip_a_long_neighbour():
    """Длинная соседка: «Нет,» в «нести» обрезала бы её начало."""
    _assert_skipped_short_line(
        ["Нет,", "Сам нести буду этот тяжёлый мешок до дома.", "Не трогай меня, слышишь."],
        "сам нести буду этот тяжелый мешок до дома не трогай меня слышишь",
    )
