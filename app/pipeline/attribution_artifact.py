"""Shadow artifact for the Attribution Auditor v0 (proposals only — never mutates)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app.pipeline.attribution_triage import SuspectLine
from app.pipeline.attribution_metrics import per_class_counts

BASE_DIR = "data/book_reports"


def build_artifact(*, book_id: str, char_map_version: int,
                   suspects: list[SuspectLine], resolutions: dict[tuple[int, int], str]) -> dict[str, Any]:
    auto_resolved: list[dict[str, Any]] = []
    needs_model: list[dict[str, Any]] = []
    for s in suspects:
        row = {
            "chapter_index": s.chapter_index,
            "line_index": s.line_index,
            "line_hash": s.line_hash,
            "current_speaker": s.current_speaker,
            "signal": s.signal,
            "decision_class": s.decision_class,
        }
        resolved = resolutions.get((s.chapter_index, s.line_index))
        if resolved:
            row["proposed_speaker"] = resolved
            auto_resolved.append(row)
        else:
            needs_model.append(row)
    return {
        "book_id": book_id,
        "char_map_version": char_map_version,
        "engine": "deterministic",
        "mode": "shadow",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "suspects": len(suspects),
            "auto_resolved": len(auto_resolved),
            "needs_model": len(needs_model),
        },
        "metrics": per_class_counts(suspects, resolutions),
        "auto_resolved": auto_resolved,
        "needs_model": needs_model,
    }


def _artifact_path(book_id: str, base_dir: str) -> str:
    return os.path.join(base_dir, book_id, "attribution_audit.json")


def write_artifact(artifact: dict[str, Any], *, base_dir: str = BASE_DIR) -> str:
    path = _artifact_path(artifact["book_id"], base_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(artifact, f, ensure_ascii=False, indent=2)
    return path


def load_artifact(book_id: str, *, base_dir: str = BASE_DIR) -> dict[str, Any]:
    with open(_artifact_path(book_id, base_dir), encoding="utf-8") as f:
        return json.load(f)
