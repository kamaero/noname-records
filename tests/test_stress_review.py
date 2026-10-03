from app.services.stress_review import aggregate_stress_review, append_stress_term


def test_aggregate_dedups_homographs_and_unresolved_across_chapters():
    reports = [
        '{"homographs": ["замок"], "unresolved": ["квазар"], "counts": {}}',
        '{"homographs": ["замок", "мука"], "unresolved": ["квазар", "плонет"], "counts": {}}',
        "",
        "not json",
    ]
    out = aggregate_stress_review(reports)
    assert out["homographs"] == ["замок", "мука"]
    assert "квазар" in out["unresolved"]
    assert out["counts"]["homographs"] == 2


def test_append_stress_term_adds_and_replaces_idempotently():
    notes = "старое=ста́рое"
    once = append_stress_term(notes, "карлица", "ка́рлица")
    assert "карлица=ка́рлица" in once and "старое=ста́рое" in once
    twice = append_stress_term(once, "Карлица", "ка́рлицА")
    assert twice.count("карлица=") == 1
    assert "карлица=ка́рлицА" in twice
