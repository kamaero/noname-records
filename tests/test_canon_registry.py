"""Cross-book canon reconcile (K1): conservative — merge only on strong evidence
(shared canonical name-token); flag everything else for the K1.5 operator gate.
Better under-merge than launder errors."""
from app.pipeline.council import CharacterEntry
from app.pipeline.canon_registry import CanonEntry, reconcile_book_into_registry


def _cast(*specs):
    # specs: (canonical, [aliases])
    return [CharacterEntry(canonical=c, aliases=list(a)) for c, a in specs]


def test_first_book_all_new():
    reg, dec = reconcile_book_into_registry([], _cast(("Дгарнин", []), ("Гамук", [])), "Байки1")
    assert {e.canonical for e in reg} == {"Дгарнин", "Гамук"}
    assert all(e.source_books == ["Байки1"] for e in reg)
    assert all(d[0] == "new" for d in dec)


def test_shared_canonical_token_merges_same_person():
    reg = [CanonEntry(canonical="Гамук Ваньянвари", source_books=["Байки1"], appears_count=1, confidence=0.5)]
    reg, dec = reconcile_book_into_registry(reg, _cast(("Гамук", ["Король Снов"])), "Байки2")
    # 'Гамук' shares token with 'Гамук Ваньянвари' -> merge, not new
    assert len(reg) == 1
    e = reg[0]
    assert "Байки2" in e.source_books and e.appears_count == 2
    assert any(a.lower() == "король снов" for a in e.aliases)
    assert e.merge_status == "same_person"
    assert dec[0][0] == "same_person"


def test_alias_overlap_without_shared_token_is_flagged_not_merged():
    # registry has Вохпкогамкиуб with alias 'Кор'; incoming canonical 'Кор' overlaps the
    # alias but shares no whole-name token -> possible_same, NEW + review_required (no auto-merge)
    reg = [CanonEntry(canonical="Вохпкогамкиуб", aliases=["Кор"], source_books=["КП"], appears_count=1)]
    reg, dec = reconcile_book_into_registry(reg, _cast(("Кор", [])), "Байки3")
    assert len(reg) == 2  # NOT merged
    new = next(e for e in reg if e.canonical == "Кор")
    assert new.review_required is True
    assert new.merge_status == "possible_same"
    assert dec[0][0] == "possible_same"


def test_authority_source_flows_through():
    reg, _ = reconcile_book_into_registry([], _cast(("Погтда", [])), "Справочник", authority="reference_doc")
    assert reg[0].authority_source == "reference_doc"
