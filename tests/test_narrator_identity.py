from app.services.shared_runtime import (
    NARRATOR_CANONICAL_KEY,
    NARRATOR_DISPLAY_NAME,
    is_narrator_name,
    narrator_aware_aggregate_key,
)


def test_is_narrator_name_matches_ru_en_and_accented():
    assert is_narrator_name("Рассказчик")
    assert is_narrator_name("Расска́зчик")  # with stress accent
    assert is_narrator_name("Narrator")
    assert is_narrator_name("NARRATOR")
    assert is_narrator_name("narrator")
    assert is_narrator_name("Автор")
    assert is_narrator_name("От автора")


def test_is_narrator_name_rejects_regular_characters():
    assert not is_narrator_name("Дгарнин")
    assert not is_narrator_name("")
    assert not is_narrator_name("Бапдиг")


def test_narrator_aggregate_key_overrides_char_map_id():
    # Three narrator surface forms with DIFFERENT char_map_ids collapse to one key
    assert narrator_aware_aggregate_key("Рассказчик", "map-1", "Рассказчик") == NARRATOR_CANONICAL_KEY
    assert narrator_aware_aggregate_key("Narrator", "map-2", "Narrator") == NARRATOR_CANONICAL_KEY
    assert narrator_aware_aggregate_key("Расска́зчик", "map-3", "Рассказчик") == NARRATOR_CANONICAL_KEY


def test_non_narrator_aggregate_key_prefers_char_map_id_then_canonical():
    assert narrator_aware_aggregate_key("Дгарнин", "map-9", "Дгарнин") == "map-9"
    assert narrator_aware_aggregate_key("Дгарнин", "", "Дгарнин") == "Дгарнин"


def test_display_name_is_recognized_as_narrator():
    # the canonical display name must itself be classified as a narrator
    assert is_narrator_name(NARRATOR_DISPLAY_NAME)
