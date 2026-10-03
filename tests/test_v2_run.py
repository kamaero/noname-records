"""The wiring both runners share: two passes, one set of numbers, one record shape."""
import json

from app.v2.attribute import NARRATOR, UNSURE
from app.v2.run import attribute_chapter, bench_record, parse_chapter_range
from app.v2.units import Unit


def _unit(ordinal, text):
    return Unit(id=f"ch1:{ordinal:05d}", chapter="ch1", ordinal=ordinal, text=text)


def _response(items, prompt_tokens=10, completion_tokens=5):
    return {"content": json.dumps({"items": items}, ensure_ascii=False),
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}}


def test_chapter_runs_both_passes_and_adds_their_numbers():
    units = [_unit(0, "Он вошёл."), _unit(1, "– Кто там?")]
    calls = []

    def llm(system, user, schema):
        calls.append(user)
        if len(calls) == 1:
            return _response([{"id": "#00000", "speaker": NARRATOR, "confidence": 0.95},
                              {"id": "#00001", "speaker": UNSURE, "confidence": 0.0}], 100, 10)
        return _response([{"id": "#00001", "speaker": "Пупип", "confidence": 0.8}], 50, 5)

    records, problems, stats = attribute_chapter(
        units, cast={"Пупип": "Пупип"}, cast_lines=["- Пупип — гоблин"], llm=llm,
    )
    assert [(r["speaker"], r["source"]) for r in records] == [(NARRATOR, "llm"), ("Пупип", "llm_review")]
    assert stats == {"calls": 2, "prompt_tokens": 150, "completion_tokens": 15}
    assert problems == []
    assert len(calls) == 2


def test_review_can_be_switched_off():
    units = [_unit(0, "– Кто там?")]
    calls = []

    def llm(system, user, schema):
        calls.append(user)
        return _response([{"id": "#00000", "speaker": UNSURE, "confidence": 0.0}])

    records, _, stats = attribute_chapter(units, cast={}, cast_lines=[], llm=llm, review=False)
    assert len(calls) == 1 and stats["calls"] == 1
    assert records[0]["speaker"] == UNSURE


def test_chapter_ranges():
    assert parse_chapter_range(None) is None
    assert parse_chapter_range("  ") is None
    assert parse_chapter_range("1-3") == {1, 2, 3}
    assert parse_chapter_range("1-3,7, 9-10") == {1, 2, 3, 7, 9, 10}


def test_bench_record_has_exactly_what_the_benchmark_reads():
    unit = _unit(4, "Текст.")
    record = {**unit.as_record(span_start=0, span_end=6, speaker=NARRATOR, confidence=0.9), "source": "llm"}
    out = bench_record(record)
    assert list(out) == ["chapter", "ordinal", "text", "span_start", "span_end", "speaker", "confidence", "source"]
    assert out["chapter"] == "ch1" and out["ordinal"] == 4 and out["speaker"] == NARRATOR
