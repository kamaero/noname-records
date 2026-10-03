import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.pipeline.attribution_triage import SuspectLine
from app.pipeline.attribution_metrics import per_class_counts


def _s(cls, signal="x"):
    return SuspectLine(chapter_index=1, line_index=0, line_hash="h",
                       current_speaker="X", signal=signal, decision_class=cls)


def test_per_class_counts_groups_by_decision_class():
    suspects = [_s("resolve_unsure"), _s("resolve_unsure"), _s("speaker_not_in_map")]
    resolutions = {}
    m = per_class_counts(suspects, resolutions)
    assert m["resolve_unsure"]["total"] == 2
    assert m["speaker_not_in_map"]["total"] == 1
    assert m["resolve_unsure"]["auto_resolved"] == 0


def test_per_class_counts_tracks_auto_resolved():
    s1 = _s("speaker_not_in_map"); s1.line_index = 3
    s2 = _s("speaker_not_in_map"); s2.line_index = 5
    m = per_class_counts([s1, s2], resolutions={(1, 3): "Дгарни́н"})
    assert m["speaker_not_in_map"]["total"] == 2
    assert m["speaker_not_in_map"]["auto_resolved"] == 1
