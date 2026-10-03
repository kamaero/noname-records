"""Driving the attribution model over a chapter, without a model.

The model is asked per batch and told the previous few paragraphs with their accepted
speakers; it answers with a dominant speaker per paragraph and, rarely, with verbatim
quotes that split a paragraph between speakers. Everything in between — batching,
prompt rendering, locating quotes, id translation, retry, the second look at what
came back weak — is deterministic and is tested here with a fake in place of the
model. Nothing here touches the network.
"""
import json

import pytest

from app.v2.attribute import (
    NARRATOR,
    SCHEMA,
    UNSURE,
    attribute_units,
    build_batches,
    parts_to_spans,
    render_user_prompt,
    review_low_confidence,
)
from app.v2.units import Unit

CAST = {"Пупип": "Пупип", "Ульганд": "Ульганд", "сэр Ульганд": "Ульганд"}
CAST_LINES = ["- Пупип — гоблин", "- Ульганд — рыцарь"]
SYSTEM = "тестовый системный промпт"


def _unit(ordinal: int, text: str, chapter: str = "ch1") -> Unit:
    return Unit(id=f"{chapter}:{ordinal:05d}", chapter=chapter, ordinal=ordinal, text=text)


def _units(n: int, text: str = "Слово."):
    return [_unit(i, f"{text} {i}") for i in range(n)]


def _response(items: list[dict], *, prompt_tokens: int = 10, completion_tokens: int = 5) -> dict:
    """What `call_chat` hands back: JSON text plus its usage block."""
    return {
        "content": json.dumps({"items": items}, ensure_ascii=False),
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "estimated": False,
        },
    }


class FakeLlm:
    """Answers every `#NNNNN` asked in the РАЗМЕТИТЬ block with one speaker."""

    def __init__(self, speaker=NARRATOR, confidence=0.9, *, responses=None):
        self.speaker = speaker
        self.confidence = confidence
        self.calls: list[tuple[str, str, dict]] = []
        self.responses = list(responses or [])

    def __call__(self, system_prompt, user_prompt, schema):
        self.calls.append((system_prompt, user_prompt, schema))
        if self.responses:
            answer = self.responses.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        asked = _asked_tokens(user_prompt)
        return _response([
            {"id": token, "speaker": self.speaker, "confidence": self.confidence} for token in asked
        ])


def _asked_tokens(user_prompt: str) -> list[str]:
    block = user_prompt.split("РАЗМЕТИТЬ", 1)[1]
    return [line.split()[0] for line in block.splitlines() if line.startswith("#")]


# --- schema -------------------------------------------------------------------


def test_schema_is_strict_and_parts_are_optional():
    item = SCHEMA["properties"]["items"]["items"]
    assert SCHEMA["additionalProperties"] is False
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"id", "speaker", "confidence"}
    assert "parts" in item["properties"] and "parts" not in item["required"]
    part = item["properties"]["parts"]["items"]
    assert set(part["required"]) == {"speaker", "quote"}


# --- batching -----------------------------------------------------------------


def test_batches_respect_the_unit_cap_and_keep_order():
    units = _units(7)
    batches = build_batches(units, max_units=3, max_chars=100_000)
    assert [len(b) for b in batches] == [3, 3, 1]
    assert [u.ordinal for b in batches for u in b] == list(range(7))


def test_batches_respect_the_char_cap():
    units = [_unit(i, "x" * 40) for i in range(5)]
    batches = build_batches(units, max_units=50, max_chars=100)
    assert [len(b) for b in batches] == [2, 2, 1]


def test_an_oversized_unit_goes_alone():
    units = [_unit(0, "a" * 10), _unit(1, "b" * 500), _unit(2, "c" * 10)]
    batches = build_batches(units, max_units=50, max_chars=100)
    assert [[u.ordinal for u in b] for b in batches] == [[0], [1], [2]]


def test_no_units_means_no_batches():
    assert build_batches([]) == []


# --- prompt -------------------------------------------------------------------


def test_prompt_has_cast_context_and_targets():
    context = [(_unit(3, "Он вошёл."), NARRATOR), (_unit(4, "– Стой! – крикнул Пупип."), "Пупип")]
    batch = [_unit(5, "– Кто там?"), _unit(6, "Тишина.")]

    prompt = render_user_prompt(batch, cast_lines=CAST_LINES, context=context)

    assert "- Пупип — гоблин" in prompt
    assert "КОНТЕКСТ" in prompt and "РАЗМЕТИТЬ" in prompt
    assert prompt.index("КОНТЕКСТ") < prompt.index("РАЗМЕТИТЬ")
    context_block = prompt.split("КОНТЕКСТ", 1)[1].split("РАЗМЕТИТЬ", 1)[0]
    assert "#00003 [Рассказчик] Он вошёл." in context_block
    assert "#00004 [Пупип]" in context_block
    assert _asked_tokens(prompt) == ["#00005", "#00006"]
    assert "#00003" not in prompt.split("РАЗМЕТИТЬ", 1)[1]


def test_prompt_without_context_has_no_context_block():
    prompt = render_user_prompt([_unit(0, "Начало.")], cast_lines=CAST_LINES, context=[])
    assert "КОНТЕКСТ" not in prompt
    assert _asked_tokens(prompt) == ["#00000"]


def test_prompt_shows_at_most_six_context_units():
    context = [(_unit(i, f"строка {i}"), NARRATOR) for i in range(10)]
    prompt = render_user_prompt([_unit(10, "цель")], cast_lines=CAST_LINES, context=context)
    block = prompt.split("КОНТЕКСТ", 1)[1].split("РАЗМЕТИТЬ", 1)[0]
    shown = [line.split()[0] for line in block.splitlines() if line.startswith("#")]
    assert shown == [f"#{i:05d}" for i in range(4, 10)]


def test_multiline_unit_text_is_flattened_so_lines_stay_one_paragraph():
    prompt = render_user_prompt([_unit(0, "первая\nвторая")], cast_lines=[], context=[])
    assert "#00000 первая вторая" in prompt


# --- parts_to_spans -----------------------------------------------------------


def test_whole_paragraph_becomes_one_span():
    unit = _unit(0, "Великан упал.")
    problems: list[str] = []
    item = parts_to_spans({"id": unit.id, "speaker": NARRATOR, "confidence": 0.95}, unit, problems)
    assert item == {"id": unit.id, "spans": [
        {"start": 0, "end": 13, "speaker": NARRATOR, "confidence": 0.95},
    ]}
    assert problems == []


def test_parts_are_located_verbatim_in_reading_order():
    unit = _unit(0, "– Не бойся, – прошептал он. – Я рядом.")
    problems: list[str] = []
    item = parts_to_spans({
        "id": unit.id, "speaker": "Ульганд", "confidence": 0.8,
        "parts": [
            {"speaker": "Ульганд", "quote": "– Не бойся, –"},
            {"speaker": NARRATOR, "quote": "прошептал он."},
            {"speaker": "Ульганд", "quote": "– Я рядом."},
        ],
    }, unit, problems)
    spans = item["spans"]
    assert [s["speaker"] for s in spans] == ["Ульганд", NARRATOR, "Ульганд"]
    assert spans[0]["start"] == 0
    assert spans[-1]["end"] == len(unit.text)
    # Spans tile the paragraph: each starts where the previous ended.
    assert all(spans[i]["end"] == spans[i + 1]["start"] for i in range(len(spans) - 1))
    assert unit.text[spans[1]["start"]:spans[1]["end"]].strip() == "прошептал он."
    assert problems == []


def test_a_quote_with_other_dashes_quotes_and_spacing_still_lands():
    unit = _unit(0, "— «Идём»,  —  сказал  Пупип.\tОн встал.")
    problems: list[str] = []
    item = parts_to_spans({
        "id": unit.id, "speaker": "Пупип", "confidence": 0.7,
        "parts": [
            {"speaker": "Пупип", "quote": '- "Идём", - сказал Пупип.'},
            {"speaker": NARRATOR, "quote": "Он встал."},
        ],
    }, unit, problems)
    spans = item["spans"]
    assert [s["speaker"] for s in spans] == ["Пупип", NARRATOR]
    assert unit.text[spans[0]["start"]:spans[0]["end"]].startswith("— «Идём»")
    assert unit.text[spans[1]["start"]:spans[1]["end"]].strip() == "Он встал."
    assert problems == []


def test_an_unlocatable_quote_falls_back_to_the_dominant_speaker():
    unit = _unit(0, "– Не бойся, – прошептал он.")
    problems: list[str] = []
    item = parts_to_spans({
        "id": unit.id, "speaker": "Ульганд", "confidence": 0.6,
        "parts": [
            {"speaker": "Ульганд", "quote": "– Не бойся, –"},
            {"speaker": NARRATOR, "quote": "этого текста тут нет"},
        ],
    }, unit, problems)
    assert item["spans"] == [{"start": 0, "end": len(unit.text), "speaker": "Ульганд", "confidence": 0.6}]
    assert len(problems) == 1 and unit.id in problems[0]


def test_gaps_between_parts_get_the_dominant_speaker():
    unit = _unit(0, "Он подумал и сказал: – Идём. И они пошли.")
    problems: list[str] = []
    item = parts_to_spans({
        "id": unit.id, "speaker": NARRATOR, "confidence": 0.9,
        "parts": [{"speaker": "Пупип", "quote": "– Идём."}],
    }, unit, problems)
    spans = item["spans"]
    assert [s["speaker"] for s in spans] == [NARRATOR, "Пупип", NARRATOR]
    assert spans[0]["start"] == 0 and spans[-1]["end"] == len(unit.text)
    assert unit.text[spans[1]["start"]:spans[1]["end"]] == "– Идём."
    assert problems == []


def test_parts_out_of_reading_order_fall_back():
    unit = _unit(0, "– Да. – Нет.")
    problems: list[str] = []
    item = parts_to_spans({
        "id": unit.id, "speaker": "Пупип", "confidence": 0.5,
        "parts": [{"speaker": "Ульганд", "quote": "– Нет."}, {"speaker": "Пупип", "quote": "– Да."}],
    }, unit, problems)
    assert len(item["spans"]) == 1 and item["spans"][0]["speaker"] == "Пупип"
    assert problems


# --- attribute_units ----------------------------------------------------------


def test_ids_are_translated_both_ways():
    units = [_unit(0, "Начало."), _unit(1, "– Привет.")]
    llm = FakeLlm(responses=[_response([
        {"id": "#00000", "speaker": NARRATOR, "confidence": 1.0},
        {"id": "#00001", "speaker": "Пупип", "confidence": 0.9},
    ])])

    records, problems, stats = attribute_units(
        units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM,
    )

    assert [(r["unit_id"], r["speaker"]) for r in records] == [("ch1:00000", NARRATOR), ("ch1:00001", "Пупип")]
    assert all(r["source"] == "llm" for r in records)
    assert problems == []
    assert llm.calls[0][0] == SYSTEM
    assert llm.calls[0][2] is SCHEMA


def test_aliases_in_the_answer_collapse_to_the_cast_name():
    units = [_unit(0, "– Вперёд!")]
    llm = FakeLlm(responses=[_response([{"id": "#00000", "speaker": "сэр Ульганд", "confidence": 0.9}])])
    records, problems, _ = attribute_units(units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM)
    assert records[0]["speaker"] == "Ульганд"
    assert problems == []


def test_a_name_the_cast_does_not_know_becomes_unsure():
    units = [_unit(0, "– Вперёд!")]
    llm = FakeLlm(responses=[_response([{"id": "#00000", "speaker": "Никто", "confidence": 0.9}])])
    records, problems, _ = attribute_units(units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM)
    assert records[0]["speaker"] == UNSURE and records[0]["confidence"] == 0.0
    assert any("Никто" in p for p in problems)


def test_context_carries_the_previous_batch_with_accepted_speakers():
    units = _units(5)
    llm = FakeLlm(speaker="Пупип")

    attribute_units(units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM, max_units=3)

    assert len(llm.calls) == 2
    first, second = llm.calls[0][1], llm.calls[1][1]
    assert "КОНТЕКСТ" not in first
    block = second.split("КОНТЕКСТ", 1)[1].split("РАЗМЕТИТЬ", 1)[0]
    assert "#00000 [Пупип]" in block and "#00002 [Пупип]" in block
    assert _asked_tokens(second) == ["#00003", "#00004"]


def test_context_answers_are_ignored_not_reported():
    """The model was told not to answer the context; if it does anyway, that is noise."""
    units = _units(4)
    llm = FakeLlm(responses=[
        _response([{"id": "#00000", "speaker": NARRATOR, "confidence": 1.0},
                   {"id": "#00001", "speaker": NARRATOR, "confidence": 1.0}]),
        _response([{"id": "#00001", "speaker": "Пупип", "confidence": 1.0},
                   {"id": "#00002", "speaker": NARRATOR, "confidence": 1.0},
                   {"id": "#00003", "speaker": NARRATOR, "confidence": 1.0}]),
    ])
    records, problems, _ = attribute_units(
        units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM, max_units=2,
    )
    assert [r["speaker"] for r in records] == [NARRATOR] * 4
    assert problems == []


def test_bad_json_is_retried_once_with_the_error_in_the_prompt():
    units = _units(2)
    good = _response([{"id": "#00000", "speaker": NARRATOR, "confidence": 1.0},
                      {"id": "#00001", "speaker": NARRATOR, "confidence": 1.0}])
    llm = FakeLlm(responses=[{"content": "не json", "usage": {"prompt_tokens": 3, "completion_tokens": 1}}, good])

    records, problems, stats = attribute_units(units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM)

    assert len(llm.calls) == 2
    assert "ОШИБКА" in llm.calls[1][1]
    assert [r["speaker"] for r in records] == [NARRATOR, NARRATOR]
    assert problems == []
    assert stats["calls"] == 2


def test_two_failures_make_the_batch_unsure_and_the_run_goes_on():
    units = _units(3)
    llm = FakeLlm(responses=[RuntimeError("timeout"), RuntimeError("timeout again")])
    llm.speaker = "Пупип"  # used for the second batch once the scripted failures run out

    records, problems, stats = attribute_units(
        units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM, max_units=2,
    )

    assert [r["speaker"] for r in records] == [UNSURE, UNSURE, "Пупип"]
    assert all(r["confidence"] == 0.0 for r in records[:2])
    assert any("timeout again" in p for p in problems)
    assert stats["calls"] == 3


def test_stats_accumulate_usage_from_the_raw_result():
    units = _units(4)
    llm = FakeLlm(responses=[
        _response([{"id": "#00000", "speaker": NARRATOR, "confidence": 1.0},
                   {"id": "#00001", "speaker": NARRATOR, "confidence": 1.0}], prompt_tokens=100, completion_tokens=7),
        _response([{"id": "#00002", "speaker": NARRATOR, "confidence": 1.0},
                   {"id": "#00003", "speaker": NARRATOR, "confidence": 1.0}], prompt_tokens=120, completion_tokens=9),
    ])
    _, _, stats = attribute_units(units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM, max_units=2)
    assert stats == {"calls": 2, "prompt_tokens": 220, "completion_tokens": 16}


def test_on_batch_is_told_about_progress():
    seen = []
    attribute_units(
        _units(5), cast=CAST, cast_lines=CAST_LINES, llm=FakeLlm(), system_prompt=SYSTEM, max_units=2,
        on_batch=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(2, 5), (4, 5), (5, 5)]


def test_silence_about_a_unit_is_unsure_not_absent():
    units = _units(2)
    llm = FakeLlm(responses=[_response([{"id": "#00000", "speaker": NARRATOR, "confidence": 1.0}])])
    records, problems, _ = attribute_units(units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM)
    assert [r["speaker"] for r in records] == [NARRATOR, UNSURE]
    assert any("промолчала" in p for p in problems)


# --- review_low_confidence ----------------------------------------------------


def _first_pass(units, speakers_and_confidence):
    records = []
    for unit, (speaker, confidence) in zip(units, speakers_and_confidence):
        record = unit.as_record(span_start=0, span_end=len(unit.text), speaker=speaker, confidence=confidence)
        record["source"] = "llm"
        records.append(record)
    return records


def test_review_targets_weak_and_unsure_units_only():
    units = _units(6)
    records = _first_pass(units, [
        (NARRATOR, 0.95), ("Пупип", 0.4), (UNSURE, 0.0), (NARRATOR, 0.9), (NARRATOR, 0.7), (NARRATOR, 0.69),
    ])
    llm = FakeLlm(speaker="Ульганд", confidence=0.85)

    out, problems, stats = review_low_confidence(
        records, units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM,
    )

    assert len(llm.calls) == 1
    assert _asked_tokens(llm.calls[0][1]) == ["#00001", "#00002", "#00005"]
    assert [r["speaker"] for r in out] == [NARRATOR, "Ульганд", "Ульганд", NARRATOR, NARRATOR, "Ульганд"]
    assert [r["source"] for r in out] == ["llm", "llm_review", "llm_review", "llm", "llm", "llm_review"]
    assert [r["unit_id"] for r in out] == [u.id for u in units]
    assert problems == []
    assert stats["calls"] == 1


def test_review_window_shows_neighbours_with_current_speakers_and_marks_the_target():
    units = _units(20)
    confidences = [(NARRATOR, 0.9)] * 20
    confidences[10] = ("Пупип", 0.3)
    records = _first_pass(units, confidences)
    llm = FakeLlm()

    review_low_confidence(records, units, radius=2, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM)

    prompt = llm.calls[0][1]
    shown = [line for line in prompt.splitlines() if line.startswith("#")]
    tokens = [line.split()[0] for line in shown]
    assert tokens[:5] == ["#00008", "#00009", "#00010", "#00011", "#00012"]
    assert "#00007" not in prompt
    target_line = next(line for line in shown if line.startswith("#00010 "))
    assert "ПЕРЕСМОТРЕТЬ" in target_line and "[Пупип]" in target_line
    assert "[Рассказчик]" in next(line for line in shown if line.startswith("#00009 "))


def test_review_batches_twenty_targets_per_call():
    units = _units(45)
    records = _first_pass(units, [(UNSURE, 0.0)] * 45)
    llm = FakeLlm()
    review_low_confidence(records, units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM)
    assert [len(_asked_tokens(call[1])) for call in llm.calls] == [20, 20, 5]


def test_review_with_nothing_weak_makes_no_call():
    units = _units(3)
    records = _first_pass(units, [(NARRATOR, 0.9)] * 3)
    llm = FakeLlm()
    out, problems, stats = review_low_confidence(records, units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM)
    assert out == records and problems == [] and stats["calls"] == 0


def test_review_keeps_the_old_answer_when_the_model_stays_silent_or_fails():
    units = _units(3)
    records = _first_pass(units, [(NARRATOR, 0.9), ("Пупип", 0.2), (UNSURE, 0.0)])
    llm = FakeLlm(responses=[
        _response([{"id": "#00001", "speaker": "Ульганд", "confidence": 0.9}]),
    ])
    out, problems, _ = review_low_confidence(records, units, cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM)
    assert [r["speaker"] for r in out] == [NARRATOR, "Ульганд", UNSURE]
    assert out[2]["source"] == "llm"
    assert any("#00002" in p or "ch1:00002" in p for p in problems)

    failing = FakeLlm(responses=[RuntimeError("down"), RuntimeError("still down")])
    out, problems, stats = review_low_confidence(records, units, cast=CAST, cast_lines=CAST_LINES, llm=failing, system_prompt=SYSTEM)
    assert out == records
    assert any("still down" in p for p in problems)
    assert stats["calls"] == 2


def test_review_uses_the_weakest_span_of_a_split_paragraph():
    unit = _unit(0, "– Да, – сказал он.")
    records = [
        {**unit.as_record(span_start=0, span_end=6, speaker="Пупип", confidence=0.95), "source": "llm"},
        {**unit.as_record(span_start=6, span_end=len(unit.text), speaker=NARRATOR, confidence=0.5), "source": "llm"},
    ]
    llm = FakeLlm(speaker="Пупип", confidence=0.9)
    out, _, _ = review_low_confidence(records, [unit], cast=CAST, cast_lines=CAST_LINES, llm=llm, system_prompt=SYSTEM)
    assert len(llm.calls) == 1
    assert [r["source"] for r in out] == ["llm_review"]
    assert out[0]["span_start"] == 0 and out[0]["span_end"] == len(unit.text)


# --- system prompt file -------------------------------------------------------


def test_the_prompt_file_is_the_only_copy_and_says_what_matters():
    from app.v2.llm import load_system_prompt

    text = load_system_prompt()
    for needle in ("Рассказчик", "UNSURE", "parts", "quote", "confidence", "не отвеча", "JSON"):
        assert needle in text, needle


def test_a_missing_prompt_file_raises(monkeypatch, tmp_path):
    from app.v2 import llm as v2_llm

    monkeypatch.setattr(v2_llm, "PROMPT_PATH", tmp_path / "nope.txt")
    with pytest.raises(FileNotFoundError):
        v2_llm.load_system_prompt()


def test_make_llm_refuses_without_an_api_key(monkeypatch):
    from app.v2 import llm as v2_llm

    monkeypatch.setattr(v2_llm, "_resolve_provider", lambda name: ("", "https://x", "openai"))
    with pytest.raises(RuntimeError, match="ключ"):
        v2_llm.make_llm("openai", "gpt-x")


def test_make_llm_calls_call_chat_with_strict_json(monkeypatch):
    from app.v2 import llm as v2_llm

    seen = {}

    def fake_call_chat(base_url, api_key, model, system_prompt, user_prompt, *, mode, force_json, json_schema, extra_body=None):
        seen.update(base_url=base_url, api_key=api_key, model=model, mode=mode,
                    force_json=force_json, json_schema=json_schema, system=system_prompt, user=user_prompt)
        return {"content": "{}", "usage": {}}

    monkeypatch.setattr(v2_llm, "_resolve_provider", lambda name: ("key", "https://x/v1", "anthropic"))
    monkeypatch.setattr(v2_llm, "call_chat", fake_call_chat)

    llm = v2_llm.make_llm("deepseek", "deepseek-v4-pro")
    assert llm("sys", "usr", SCHEMA) == {"content": "{}", "usage": {}}
    assert seen["force_json"] is True and seen["json_schema"] is SCHEMA
    assert seen["mode"] == "anthropic" and seen["api_key"] == "key" and seen["model"] == "deepseek-v4-pro"
