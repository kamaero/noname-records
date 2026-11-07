from app.services.character_names import (
    _is_pure_title_name,
    _is_valid_dialogue_speaker_name,
    _normalize_character_identity_key,
)


def test_accented_names_are_distinct_and_unicode_normalized():
    assert _normalize_character_identity_key("Éva") != _normalize_character_identity_key("Va")
    assert _normalize_character_identity_key("Éva") == _normalize_character_identity_key("E\u0301va")
    assert _is_valid_dialogue_speaker_name("Éva")


def test_english_shared_title_is_not_a_merge_bridge():
    assert _is_pure_title_name("King")
    assert _is_pure_title_name("Lord")
    assert not _is_pure_title_name("King Arthur")
