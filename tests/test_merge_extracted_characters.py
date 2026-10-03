from app.services.char_extraction import _merge_extracted_characters
from app.services.character_names import _is_pure_title_name


def _names(merged):
    return {m["canonical_name"] for m in merged}


def test_no_merge_on_shared_generic_title():
    # Two distinct demonlords share only the title "барон" as an alias.
    # They must NOT collapse into one (the snowball bug).
    merged = _merge_extracted_characters([
        {"canonical_name": "Хубол", "aliases": ["Банкир", "барон"]},
        {"canonical_name": "Инвнехлизад", "aliases": ["Лимми", "барон"]},
    ])
    assert _names(merged) == {"Хубол", "Инвнехлизад"}


def test_no_transitive_snowball_via_titles():
    # Three different characters chained only by shared titles must stay three.
    merged = _merge_extracted_characters([
        {"canonical_name": "Хубол", "aliases": ["барон", "Купец"]},
        {"canonical_name": "Бимькмолепус", "aliases": ["Купец", "Балаганщик"]},
        {"canonical_name": "Асмодиус", "aliases": ["Балаганщик", "Тёмный Господин"]},
    ])
    assert _names(merged) == {"Хубол", "Бимькмолепус", "Асмодиус"}


def test_merge_on_canonical_name_match():
    # Same character, different canonical choice across chunks — SHOULD merge.
    merged = _merge_extracted_characters([
        {"canonical_name": "Дгарнин", "aliases": ["Лис"]},
        {"canonical_name": "Лис", "aliases": ["Принц Тьмы"]},
    ])
    assert len(merged) == 1
    assert "Принц Тьмы" in merged[0]["aliases"]


def test_merge_when_canonical_appears_as_alias():
    # "Хубол" canonical matches the "Хубол" alias of "Банкир Хубол".
    merged = _merge_extracted_characters([
        {"canonical_name": "Хубол", "aliases": ["барон"]},
        {"canonical_name": "Банкир Хубол", "aliases": ["Хубол"]},
    ])
    assert len(merged) == 1


def test_title_stays_a_legit_alias():
    # The fix must not strip titles — Вохпкогамкиуб keeps «Тёмный Господин».
    merged = _merge_extracted_characters([
        {"canonical_name": "Вохпкогамкиуб", "aliases": ["Тёмный Господин", "Кор"]},
    ])
    assert merged[0]["aliases"] == sorted(["Тёмный Господин", "Кор"])


def test_pure_title_helper():
    assert _is_pure_title_name("Тёмный Господин")
    assert _is_pure_title_name("барон")
    assert not _is_pure_title_name("Вохпкогамкиуб")
    assert not _is_pure_title_name("Банкир Хубол")  # has a proper name
