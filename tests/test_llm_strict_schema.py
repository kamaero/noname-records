"""A schema sent with `strict: true` must satisfy what strict actually means.

Our schema left `parts` out of `required` — DeepSeek and Anthropic accept that, and
OpenAI-compatible providers that enforce strict mode reject the whole request:
«'required' is required to be supplied and to be an array including every key in
properties. Missing 'parts'». The request never reached the model, so an evaluation
of that model measured nothing.

Widening the optional key to accept null is the counterpart: a model that has nothing
to put there must still be able to answer.
"""
from app.pipeline.llm_client import strict_json_schema
from app.v2.attribute import SCHEMA


def test_every_property_is_required_at_every_level():
    strict = strict_json_schema(SCHEMA)

    item = strict["properties"]["items"]["items"]
    assert set(item["required"]) == set(item["properties"])
    part = item["properties"]["parts"]["items"]
    assert set(part["required"]) == set(part["properties"])


def test_a_key_that_was_optional_may_now_be_null():
    strict = strict_json_schema(SCHEMA)

    parts = strict["properties"]["items"]["items"]["properties"]["parts"]
    assert parts["type"] == ["array", "null"]
    # …and one that was already required keeps its plain type
    assert strict["properties"]["items"]["items"]["properties"]["speaker"]["type"] == "string"


def test_the_original_schema_is_not_touched():
    before = repr(SCHEMA)
    strict_json_schema(SCHEMA)
    assert repr(SCHEMA) == before


def test_the_parser_reads_a_null_parts_as_no_parts():
    from app.v2.attribute import parts_to_spans
    from app.v2.units import Unit

    unit = Unit(id="u1", chapter="ch-1", ordinal=0, text="- Привет, - сказал Дгарнин.")
    problems: list[str] = []

    result = parts_to_spans({"id": "u1", "speaker": "Дгарнин", "confidence": 0.9, "parts": None}, unit, problems)

    assert result["spans"] == [{"start": 0, "end": len(unit.text), "speaker": "Дгарнин", "confidence": 0.9}]
    assert problems == []
