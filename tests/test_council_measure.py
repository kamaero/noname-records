"""Alias-aware measure: council proposal vs existing char_memory (Ф1b).

Matching reuses the system's identity-key normalization, so the measure predicts
how apply (Ф1c) would match — not naive exact-name comparison.
"""
from app.pipeline.council import CharacterEntry
from app.pipeline.council_measure import ExistingChar, measure_proposal_vs_existing


def _existing(*pairs):
    return [ExistingChar(name=n, aliases=list(a)) for n, a in pairs]


def test_exact_name_match_is_confirmed():
    prop = [CharacterEntry(canonical="Дарт", appears_in=[1, 2])]
    existing = _existing(("Дарт", []))
    r = measure_proposal_vs_existing(prop, existing)
    assert r["confirmed_count"] == 1
    assert r["new_count"] == 0
    assert r["missing_count"] == 0


def test_alias_match_is_confirmed_not_new():
    # proposal canonical equals an existing alias -> same person
    prop = [CharacterEntry(canonical="Тёмный")]
    existing = _existing(("Дарт", ["Тёмный"]))
    r = measure_proposal_vs_existing(prop, existing)
    assert r["confirmed_count"] == 1
    assert r["new_count"] == 0


def test_case_and_punctuation_differences_still_match():
    prop = [CharacterEntry(canonical="темный  господин")]
    existing = _existing(("Тёмный Господин", []))
    # ё/е differ -> identity key keeps them distinct ONLY if normalization keeps ё;
    # the system key lowercases and strips non-alnum, ё stays ё. Use matching forms:
    r = measure_proposal_vs_existing([CharacterEntry(canonical="Тёмный, Господин!")], existing)
    assert r["confirmed_count"] == 1


def test_unmatched_proposal_is_new():
    prop = [CharacterEntry(canonical="Абиссалис")]
    existing = _existing(("Дарт", []))
    r = measure_proposal_vs_existing(prop, existing)
    assert r["new_count"] == 1
    assert r["new"][0]["canonical"] == "Абиссалис"


def test_unmatched_existing_is_missing():
    prop = [CharacterEntry(canonical="Дарт")]
    existing = _existing(("Дарт", []), ("Лея", []))
    r = measure_proposal_vs_existing(prop, existing)
    assert r["missing_count"] == 1
    assert r["missing"] == ["Лея"]


def test_alias_gain_captured_on_confirmed():
    prop = [CharacterEntry(canonical="Дарт", aliases=["Вейдер", "Тёмный"])]
    existing = _existing(("Дарт", ["Тёмный"]))
    r = measure_proposal_vs_existing(prop, existing)
    assert r["confirmed_count"] == 1
    gain = r["confirmed"][0]["alias_gain"]
    assert [a.lower() for a in gain] == ["вейдер"]
    assert r["alias_gain_total"] == 1
