from __future__ import annotations

import json

from app.pronunciation import _strip_stress, apply_pronunciation_dictionary


def aggregate_stress_review(chapter_reports: list[str]) -> dict:
    homographs: set[str] = set()
    unresolved: set[str] = set()
    for raw in chapter_reports or []:
        try:
            data = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        homographs.update(str(w) for w in (data.get("homographs") or []) if str(w).strip())
        unresolved.update(str(w) for w in (data.get("unresolved") or []) if str(w).strip())
    return {
        "homographs": sorted(homographs),
        "unresolved": sorted(unresolved),
        "counts": {"homographs": len(homographs), "unresolved": len(unresolved)},
    }


def append_stress_term(notes: str, word: str, stressed: str) -> str:
    key = _strip_stress((word or "").strip()).casefold()
    if not key or not (stressed or "").strip():
        return notes or ""
    kept: list[str] = []
    for line in (notes or "").splitlines():
        raw = line.strip()
        if not raw or "=" not in raw:
            if raw:
                kept.append(line)
            continue
        src = _strip_stress(raw.split("=", 1)[0].strip()).casefold()
        if src != key:
            kept.append(line)
    kept.append(f"{key}={stressed.strip()}")
    return "\n".join(kept)


