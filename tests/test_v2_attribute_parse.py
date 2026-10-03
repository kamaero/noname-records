"""Reading what the model answered, and refusing to believe the impossible parts.

The model is asked to annotate, not to rewrite, so its answer is small and every claim
in it is checkable against the text we already hold: a span must lie inside its
segment, spans of one segment must not overlap, and a speaker must be someone the cast
knows — or «Рассказчик», or «UNSURE» when the model declines.

A malformed answer must never take the chapter down with it. The old pipeline's habit
was to raise from deep inside a part and degrade the whole chapter; here a segment the
model got wrong about becomes UNSURE with confidence zero and the rest stands.
"""
from app.v2.attribute import NARRATOR, UNSURE, parse_response
from app.v2.units import Unit


def _units():
    return [
        Unit(id="ch1:00000", chapter="ch1", ordinal=0, text="– Не бойся, – прошептал он."),
        Unit(id="ch1:00001", chapter="ch1", ordinal=1, text="Великан упал."),
    ]


CAST = {"Ульганд", "Вукьрадух"}


def test_a_clean_answer_becomes_records():
    payload = {"items": [
        {"id": "ch1:00000", "spans": [
            {"start": 0, "end": 11, "speaker": "Ульганд", "confidence": 0.9},
            {"start": 11, "end": 27, "speaker": NARRATOR, "confidence": 0.95},
        ]},
        {"id": "ch1:00001", "spans": [{"start": 0, "end": 13, "speaker": NARRATOR, "confidence": 1.0}]},
    ]}

    records, problems = parse_response(payload, _units(), cast=CAST)

    assert [r["speaker"] for r in records] == ["Ульганд", NARRATOR, NARRATOR]
    assert records[0]["ordinal"] == 0
    assert problems == []


def test_a_span_reaching_past_the_text_is_clipped():
    payload = {"items": [{"id": "ch1:00001", "spans": [
        {"start": 0, "end": 999, "speaker": NARRATOR, "confidence": 1.0},
    ]}]}

    records, problems = parse_response(payload, _units(), cast=CAST)

    voiced = [r for r in records if r["ordinal"] == 1]
    assert voiced[0]["span_end"] == len("Великан упал.")
    assert any("clipped" in p for p in problems)


def test_overlapping_spans_keep_the_first_and_report_it():
    payload = {"items": [{"id": "ch1:00000", "spans": [
        {"start": 0, "end": 20, "speaker": "Ульганд", "confidence": 0.9},
        {"start": 10, "end": 27, "speaker": NARRATOR, "confidence": 0.9},
    ]}]}

    records, problems = parse_response(payload, _units(), cast=CAST)

    first = [r for r in records if r["ordinal"] == 0]
    assert [(r["span_start"], r["span_end"]) for r in first] == [(0, 20), (20, 27)]
    assert any("overlap" in p for p in problems)


def test_a_speaker_the_cast_does_not_know_becomes_unsure():
    """An invented name is the failure mode that matters: it would put a replica in
    the mouth of somebody who does not exist, and nobody would notice at the desk."""
    payload = {"items": [{"id": "ch1:00001", "spans": [
        {"start": 0, "end": 13, "speaker": "Некто Выдуманный", "confidence": 0.99},
    ]}]}

    records, problems = parse_response(payload, _units(), cast=CAST)

    voiced = [r for r in records if r["ordinal"] == 1]
    assert voiced[0]["speaker"] == UNSURE
    assert voiced[0]["confidence"] == 0.0
    assert any("Некто Выдуманный" in p for p in problems)


def test_the_narrator_is_always_allowed():
    payload = {"items": [{"id": "ch1:00001", "spans": [
        {"start": 0, "end": 13, "speaker": NARRATOR, "confidence": 0.5},
    ]}]}

    records, _problems = parse_response(payload, _units(), cast=set())

    voiced = [r for r in records if r["ordinal"] == 1]
    assert voiced[0]["speaker"] == NARRATOR


def test_a_segment_the_model_skipped_becomes_unsure_not_missing():
    """Silence about a segment is not the same as having no opinion worth recording:
    the benchmark counts uncovered text against the model, so it has to be visible."""
    payload = {"items": [{"id": "ch1:00000", "spans": [
        {"start": 0, "end": 27, "speaker": "Ульганд", "confidence": 0.9},
    ]}]}

    records, problems = parse_response(payload, _units(), cast=CAST)

    by_ordinal = {r["ordinal"]: r for r in records}
    assert by_ordinal[1]["speaker"] == UNSURE
    assert any("ch1:00001" in p for p in problems)


def test_an_id_the_batch_never_contained_is_dropped():
    payload = {"items": [{"id": "ch9:99999", "spans": [
        {"start": 0, "end": 5, "speaker": "Ульганд", "confidence": 0.9},
    ]}]}

    records, problems = parse_response(payload, _units(), cast=CAST)

    assert all(r["unit_id"] != "ch9:99999" for r in records)
    assert any("ch9:99999" in p for p in problems)


def test_rubbish_does_not_raise():
    for payload in (None, {}, {"items": None}, {"items": [None]}, {"items": [{"id": "ch1:00000"}]}):
        records, problems = parse_response(payload, _units(), cast=CAST)
        assert all(r["speaker"] == UNSURE for r in records)
        assert problems
