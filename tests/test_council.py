from app.pipeline.council import CastProposal, CharacterEntry, normalize_cast_output, cast_disagreement, run_council_shadow


def test_normalize_extracts_characters_aliases_and_evidence_count():
    raw = {
        "characters": [
            {"canonical": "Дарт", "aliases": ["Тёмный", "тёмный"], "evidence": ["e1", "e2"]},
            {"canonical": "Лея", "aliases": [], "evidence": ["e3"]},
        ]
    }
    prop = normalize_cast_output("gemini", raw)
    assert isinstance(prop, CastProposal)
    assert prop.source == "gemini"
    names = {c.canonical for c in prop.characters}
    assert names == {"Дарт", "Лея"}
    darth = next(c for c in prop.characters if c.canonical == "Дарт")
    assert isinstance(darth, CharacterEntry)
    assert [a.lower() for a in darth.aliases].count("тёмный") == 1
    assert darth.evidence_count == 2


def test_normalize_is_robust_to_missing_fields():
    prop = normalize_cast_output("x", {"characters": [{"canonical": "А"}, {"aliases": ["y"]}]})
    assert [c.canonical for c in prop.characters] == ["А"]
    assert prop.characters[0].aliases == []
    assert prop.characters[0].evidence_count == 0


def test_normalize_handles_empty():
    assert normalize_cast_output("x", {}).characters == []


def _p(source, chars):
    return normalize_cast_output(source, {"characters": chars})


def test_disagreement_counts_cast_symmetric_difference():
    a = _p("a", [{"canonical": "Дарт"}, {"canonical": "Лея"}])
    b = _p("b", [{"canonical": "Дарт"}, {"canonical": "Хан"}])
    d = cast_disagreement(a, b)
    assert d["cast_diff"] == 2
    assert set(d["only_in_a"]) == {"Лея"}
    assert set(d["only_in_b"]) == {"Хан"}


def test_disagreement_counts_alias_owner_conflicts():
    a = _p("a", [{"canonical": "Дарт", "aliases": ["Тёмный"]}])
    b = _p("b", [{"canonical": "Энакин", "aliases": ["Тёмный"]}])
    d = cast_disagreement(a, b)
    assert d["alias_diff"] == 1


def test_disagreement_zero_when_identical():
    a = _p("a", [{"canonical": "Дарт", "aliases": ["Тёмный"]}])
    b = _p("b", [{"canonical": "Дарт", "aliases": ["тёмный"]}])
    d = cast_disagreement(a, b)
    assert d["cast_diff"] == 0
    assert d["alias_diff"] == 0
    assert d["scalar"] == 0


def test_run_council_shadow_collects_participants_arbiter_and_disagreement():
    casts = {
        "m-a": {"characters": [{"canonical": "Дарт"}, {"canonical": "Лея"}]},
        "m-b": {"characters": [{"canonical": "Дарт"}, {"canonical": "Хан"}]},
        "arb": {"characters": [{"canonical": "Дарт"}, {"canonical": "Лея"}, {"canonical": "Хан"}]},
    }
    calls = []

    def run_fn(provider, model, system, user, schema):
        calls.append(model)
        return casts[model]

    result = run_council_shadow(
        book_text="книга",
        members=[("A", "p", "m-a"), ("B", "p", "m-b")],
        arbiter=("ARB", "p", "arb"),
        run_fn=run_fn,
    )
    assert [p.source for p in result["participants"]] == ["A", "B"]
    assert result["arbiter"].source == "ARB"
    assert {c.canonical for c in result["arbiter"].characters} == {"Дарт", "Лея", "Хан"}
    assert result["disagreement"]["cast_diff"] == 2
    assert calls == ["m-a", "m-b", "arb"]


def test_run_council_shadow_survives_member_error():
    def run_fn(provider, model, system, user, schema):
        if model == "boom":
            raise RuntimeError("provider exploded")
        return {"characters": [{"canonical": "Дарт"}]}

    result = run_council_shadow(
        book_text="книга",
        members=[("A", "p", "ok"), ("B", "p", "boom")],
        arbiter=("ARB", "p", "ok"),
        run_fn=run_fn,
    )
    b = next(p for p in result["participants"] if p.source == "B")
    assert b.characters == []
    assert "provider exploded" in result["errors"]["B"]


# --- range-based recall by chapter (appears_in) ---
from app.pipeline.council import merge_cast_proposals, run_council_charext_chunked


def test_normalize_sets_appears_in_from_chapter_index():
    p = normalize_cast_output("g", {"characters": [{"canonical": "Дарт"}]}, chapter_index=3)
    assert p.characters[0].appears_in == [3]


def test_normalize_appears_in_empty_without_chapter_index():
    p = normalize_cast_output("g", {"characters": [{"canonical": "Дарт"}]})
    assert p.characters[0].appears_in == []


def test_merge_cast_proposals_dedups_unions_aliases_and_appears_in():
    p1 = normalize_cast_output("g", {"characters": [{"canonical": "Дарт", "aliases": ["Тёмный"]}]}, chapter_index=1)
    p2 = normalize_cast_output(
        "g",
        {"characters": [{"canonical": "Дарт", "aliases": ["тёмный", "Вейдер"]}, {"canonical": "Лея"}]},
        chapter_index=2,
    )
    merged = merge_cast_proposals("g", [p1, p2])
    assert {c.canonical for c in merged.characters} == {"Дарт", "Лея"}
    darth = next(c for c in merged.characters if c.canonical == "Дарт")
    assert sorted(darth.appears_in) == [1, 2]
    assert sorted(a.lower() for a in darth.aliases) == ["вейдер", "тёмный"]


def test_run_council_charext_chunked_recall_merge_and_arbiter():
    per_chapter = {
        1: {"characters": [{"canonical": "Дарт", "aliases": ["Тёмный"]}]},
        2: {"characters": [{"canonical": "Дарт"}, {"canonical": "Лея"}]},
    }
    arbiter_out = {"characters": [{"canonical": "Дарт", "aliases": ["Тёмный"]}, {"canonical": "Лея"}]}
    calls = []

    def run_fn(provider, model, system, user, schema):
        calls.append(model)
        if model == "rec":
            for idx, payload in per_chapter.items():
                if f"CH{idx}" in user:
                    return payload
            return {"characters": []}
        return arbiter_out

    result = run_council_charext_chunked(
        chapters=[(1, "CH1 text"), (2, "CH2 text")],
        recaller=("gemini", "p", "rec"),
        arbiter=("arb", "p", "arb"),
        run_fn=run_fn,
    )
    merged = result["participants"][0]
    assert {c.canonical for c in merged.characters} == {"Дарт", "Лея"}
    darth = next(c for c in merged.characters if c.canonical == "Дарт")
    assert sorted(darth.appears_in) == [1, 2]
    assert result["arbiter"].source == "arb"
    assert calls.count("rec") == 2 and calls.count("arb") == 1


# --- arbiter merge guard (precision) ---
from app.pipeline.council import guard_arbiter_merges


def test_guard_splits_alias_that_is_another_recall_canonical():
    recall = CastProposal("rec", [CharacterEntry("Дгарнин"), CharacterEntry("Сатухух")])
    arbiter = CastProposal("arb", [CharacterEntry("Дгарнин", aliases=["Лис", "Сатухух"])])
    guarded, splits = guard_arbiter_merges(arbiter, recall)
    dz = next(c for c in guarded.characters if c.canonical == "Дгарнин")
    assert all(a.lower() != "сатухух" for a in dz.aliases)  # bad merge split out
    assert "Лис" in dz.aliases                              # legit alias kept
    assert any(c.canonical.lower() == "сатухух" for c in guarded.characters)  # restored own
    assert len(splits) == 1


def test_guard_keeps_legit_aliases_no_splits():
    recall = CastProposal("rec", [CharacterEntry("Дгарнин")])
    arbiter = CastProposal("arb", [CharacterEntry("Дгарнин", aliases=["Лис", "Пресвитер"])])
    guarded, splits = guard_arbiter_merges(arbiter, recall)
    assert splits == []
    assert sorted(guarded.characters[0].aliases) == ["Лис", "Пресвитер"]


def test_guard_keeps_same_person_name_variant_shared_token():
    # 'Гамук' is its own recall canonical, but it shares token with 'Гамук Ваньянвари'
    # -> same person, must NOT be split out.
    recall = CastProposal("rec", [CharacterEntry("Гамук Ваньянвари"), CharacterEntry("Гамук")])
    arbiter = CastProposal("arb", [CharacterEntry("Гамук Ваньянвари", aliases=["Гамук", "Сомнамбула"])])
    guarded, splits = guard_arbiter_merges(arbiter, recall)
    assert splits == []  # shared token -> no false split
    tak = guarded.characters[0]
    assert "Гамук" in tak.aliases
