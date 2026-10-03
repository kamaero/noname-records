"""Per decision-class counts for the Attribution Auditor v0 bench (no LLM)."""
from __future__ import annotations

from typing import Any

from app.pipeline.attribution_triage import SuspectLine


def per_class_counts(suspects: list[SuspectLine], resolutions: dict[tuple[int, int], str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for s in suspects:
        bucket = out.setdefault(s.decision_class, {"total": 0, "auto_resolved": 0})
        bucket["total"] += 1
        if resolutions.get((s.chapter_index, s.line_index)):
            bucket["auto_resolved"] += 1
    return out
