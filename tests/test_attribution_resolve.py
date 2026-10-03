import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.pipeline.attribution_resolve import build_cast_index, resolve_label, Resolution


def test_build_cast_index_maps_norm_to_canonical_display():
    cast = [("Дгарни́н", ["Dgarnin", "Кислятина"]), ("Пупип", [])]
    idx = build_cast_index(cast)
    from app.pipeline.attribution_triage import normalize_label
    assert idx[normalize_label("Dgarnin")] == "Дгарни́н"
    assert idx[normalize_label("Кислятина")] == "Дгарни́н"
    assert idx[normalize_label("Пупип")] == "Пупип"


def test_resolve_unique_garbled_label():
    cast = [("Дгарни́н", ["Dgarnin"]), ("Пупип", [])]
    idx = build_cast_index(cast)
    r = resolve_label("Dgarnin", idx)
    assert isinstance(r, Resolution)
    assert r.resolved == "Дгарни́н"
    assert r.ambiguous is False


def test_resolve_returns_none_when_no_cast_match():
    idx = build_cast_index([("Дгарни́н", [])])
    r = resolve_label("Незнакомец", idx)
    assert r.resolved is None
    assert r.ambiguous is False


def test_resolve_flags_ambiguous_when_two_canonicals_share_a_norm():
    # two distinct canonicals collapse to the same accent-stripped key -> cannot auto-resolve
    cast = [("Арбор", []), ("Арбо́р", [])]
    idx = build_cast_index(cast)
    r = resolve_label("Арбор", idx)   # Cyrillic query; no translit needed
    assert r.resolved is None
    assert r.ambiguous is True
