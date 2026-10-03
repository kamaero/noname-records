from __future__ import annotations

from app.pipeline.llm_client import _extract_json_object


def test_extract_plain_object():
    assert _extract_json_object('{"a": 1}') == '{"a": 1}'


def test_extract_strips_code_fences():
    text = '```json\n{"a": 1, "b": "x"}\n```'
    assert _extract_json_object(text) == '{"a": 1, "b": "x"}'


def test_extract_object_from_surrounding_prose():
    text = 'Вот результат: {"name": "Иван"} — готово.'
    assert _extract_json_object(text) == '{"name": "Иван"}'


def test_extract_returns_stripped_when_no_object():
    assert _extract_json_object("  no json here  ") == "no json here"
