"""K4: alias-aware overlay of the author cast (actor + reply colour) onto canon."""
from app.services.cast_overlay import build_cast_index, match_cast


def test_matches_by_canonical_name():
    idx = build_cast_index([("Дгарнин", [], "Филатов Павел", "#abc")])
    assert match_cast("Дгарнин", [], idx) == ("Филатов Павел", "#abc")


def test_matches_via_alias_overlap():
    idx = build_cast_index([("Вохпкогамкиуб", ["Кор"], "Иванов", "")])
    # canon entry whose alias overlaps the cast canonical/alias
    assert match_cast("Кор", [], idx) == ("Иванов", "")


def test_no_match_returns_empty():
    idx = build_cast_index([("Дгарнин", [], "Филатов", "")])
    assert match_cast("Незнакомец", [], idx) == ("", "")


def test_case_and_punctuation_insensitive():
    idx = build_cast_index([("Тёмный Господин", [], "Широков", "")])
    assert match_cast("тёмный, господин!", [], idx) == ("Широков", "")
