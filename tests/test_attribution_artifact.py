import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.pipeline.attribution_triage import SuspectLine
from app.pipeline.attribution_artifact import build_artifact, write_artifact, load_artifact


def _suspect(idx, signal, cls):
    return SuspectLine(chapter_index=1, line_index=idx, line_hash="sha256:abc",
                       current_speaker="X", signal=signal, decision_class=cls)


def test_build_artifact_splits_resolved_vs_needs_model():
    suspects = [_suspect(3, "garbled_label", "speaker_not_in_map"),
                _suspect(4, "unsure_marker", "resolve_unsure")]
    resolutions = {(1, 3): "Дгарни́н"}
    art = build_artifact(book_id="b1", char_map_version=7, suspects=suspects, resolutions=resolutions)
    assert art["book_id"] == "b1"
    assert art["mode"] == "shadow"
    assert art["summary"]["auto_resolved"] == 1
    assert art["summary"]["needs_model"] == 1
    assert art["auto_resolved"][0]["line_index"] == 3
    assert art["auto_resolved"][0]["proposed_speaker"] == "Дгарни́н"
    assert art["needs_model"][0]["line_index"] == 4


def test_write_and_load_artifact_roundtrip():
    art = build_artifact(book_id="b1", char_map_version=1, suspects=[], resolutions={})
    with tempfile.TemporaryDirectory() as d:
        path = write_artifact(art, base_dir=d)
        assert os.path.exists(path)
        loaded = load_artifact("b1", base_dir=d)
        assert loaded["book_id"] == "b1"
        assert loaded["summary"]["auto_resolved"] == 0


from app.pipeline.attribution_metrics import per_class_counts


def test_artifact_includes_per_class_metrics():
    s = SuspectLine(chapter_index=1, line_index=3, line_hash="h",
                    current_speaker="Dgarnin", signal="garbled_label",
                    decision_class="speaker_not_in_map")
    art = build_artifact(book_id="b1", char_map_version=1, suspects=[s],
                         resolutions={(1, 3): "Дгарни́н"})
    assert art["metrics"]["speaker_not_in_map"]["total"] == 1
    assert art["metrics"]["speaker_not_in_map"]["auto_resolved"] == 1
