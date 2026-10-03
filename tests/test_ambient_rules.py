"""Чистые правила эмбиента: зажим длины, имена файлов, промпт для Opus и разбор ответа."""
import pytest

from app.pipeline.ambient import (
    PROMPT_SCHEMA,
    PROMPT_SYSTEM,
    ambient_file_names,
    clean_file_stem,
    parse_prompt,
    prompt_user,
    track_seconds,
)


# --- track_seconds -----------------------------------------------------------------

def test_track_seconds_clamps_below_minimum():
    assert track_seconds(10) == 180


def test_track_seconds_clamps_above_maximum():
    assert track_seconds(900) == 600


def test_track_seconds_keeps_value_inside_range():
    assert track_seconds(300) == 300


def test_track_seconds_rounds_to_int():
    assert track_seconds(300.6) == 301


def test_track_seconds_handles_falsy_input():
    assert track_seconds(0) == 180
    assert track_seconds(None) == 180


# --- clean_file_stem -----------------------------------------------------------------

def test_clean_file_stem_strips_forbidden_characters():
    assert clean_file_stem('Тав: "Ворота" / кабак *?<>|') == "Тав Ворота кабак"


def test_clean_file_stem_strips_control_characters():
    assert clean_file_stem("Лес\x00\x07ная\x7fполяна") == "Леснаяполяна"


def test_clean_file_stem_collapses_whitespace_keeps_yo_and_spaces():
    assert clean_file_stem("Тёмный   лес   у   озера") == "Тёмный лес у озера"


def test_clean_file_stem_truncates_to_100():
    long_name = "А" * 150
    result = clean_file_stem(long_name)
    assert len(result) == 100


def test_clean_file_stem_handles_empty():
    assert clean_file_stem("") == ""
    assert clean_file_stem(None) == ""


# --- ambient_file_names -----------------------------------------------------------------

def test_ambient_file_names_basic():
    names = ambient_file_names(5, ["Таверна", "Лес"])
    assert names == [
        "Г5_Эмбиент - Таверна.mp3",
        "Г5_Эмбиент - Лес.mp3",
    ]


def test_ambient_file_names_numbers_repeats_in_order():
    names = ambient_file_names(3, ["Таверна", "Лес", "Таверна", "Таверна", "Лес"])
    assert names == [
        "Г3_Эмбиент - Таверна.mp3",
        "Г3_Эмбиент - Лес.mp3",
        "Г3_Эмбиент - Таверна 2.mp3",
        "Г3_Эмбиент - Таверна 3.mp3",
        "Г3_Эмбиент - Лес 2.mp3",
    ]


def test_ambient_file_names_cleans_place_names():
    names = ambient_file_names(1, ['Таверна "У ворот"'])
    assert names == ["Г1_Эмбиент - Таверна У ворот.mp3"]


def test_ambient_file_names_empty_place_becomes_without_place():
    names = ambient_file_names(2, ['/:*?"<>|', "", None, "Лес"])
    assert names == [
        "Г2_Эмбиент - без места.mp3",
        "Г2_Эмбиент - без места 2.mp3",
        "Г2_Эмбиент - без места 3.mp3",
        "Г2_Эмбиент - Лес.mp3",
    ]


def test_ambient_file_names_empty_list():
    assert ambient_file_names(1, []) == []


# --- prompt_user -----------------------------------------------------------------

def test_prompt_user_contains_place_mood_length_and_previous_summaries():
    scene = {
        "place": {"name": "Старая таверна", "description": "прокуренный зал"},
        "time_of_day": "ночь",
        "weather": "дождь",
        "ambience": "гул голосов",
        "mood": "тревожное ожидание",
        "music_queries": ["tavern ambience", "medieval strings"],
    }
    text = "Дверь скрипнула, и в зал ворвался холодный воздух."
    previous = ["low drone, sparse piano, slow tempo", "solo cello, sustained pads, very slow"]

    result = prompt_user(scene, text, 240, previous)

    assert "Старая таверна" in result
    assert "тревожное ожидание" in result
    assert "240" in result
    for summary in previous:
        assert summary in result
    assert text in result


def test_prompt_user_handles_missing_fields_and_no_previous():
    result = prompt_user({}, "", 180, [])
    assert "180" in result
    assert "прежних треков ещё нет" in result


def test_prompt_system_mentions_no_vocals_and_word_range():
    assert "вокал" in PROMPT_SYSTEM.lower()
    assert "40-80" in PROMPT_SYSTEM or "40–80" in PROMPT_SYSTEM


def test_prompt_schema_shape():
    assert PROMPT_SCHEMA["required"] == ["prompt", "summary"]
    assert set(PROMPT_SCHEMA["properties"]) == {"prompt", "summary"}


# --- parse_prompt -----------------------------------------------------------------

def test_parse_prompt_from_dict():
    prompt, summary = parse_prompt({"prompt": "calm ambient strings", "summary": "strings · pads · slow"})
    assert prompt == "calm ambient strings"
    assert summary == "strings · pads · slow"


def test_parse_prompt_from_json_string():
    prompt, summary = parse_prompt('{"prompt": "soft piano", "summary": "piano · sparse · slow"}')
    assert prompt == "soft piano"
    assert summary == "piano · sparse · slow"


def test_parse_prompt_strips_whitespace():
    prompt, summary = parse_prompt({"prompt": "  p  ", "summary": "  s  "})
    assert prompt == "p"
    assert summary == "s"


@pytest.mark.parametrize("answer", ["", "   ", "not json", "[]", "null", 42, None])
def test_parse_prompt_raises_on_broken_answer(answer):
    with pytest.raises(ValueError):
        parse_prompt(answer)


def test_parse_prompt_raises_on_missing_fields():
    with pytest.raises(ValueError):
        parse_prompt({"prompt": "only prompt"})
    with pytest.raises(ValueError):
        parse_prompt({"summary": "only summary"})
    with pytest.raises(ValueError):
        parse_prompt({"prompt": "", "summary": ""})
