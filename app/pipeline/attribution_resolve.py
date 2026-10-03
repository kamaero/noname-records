"""Deterministic resolution of a garbled cue label to a unique cast member (v0, no LLM)."""
from __future__ import annotations

from dataclasses import dataclass

from app.pipeline.attribution_triage import normalize_label


@dataclass
class Resolution:
    resolved: str | None  # canonical display name, or None if unresolved
    ambiguous: bool       # True if the norm key maps to >1 distinct canonical


def build_cast_index(cast: list[tuple[str, list[str]]]) -> dict[str, str]:
    """norm(name|alias) -> canonical display name. Collisions across distinct canonicals
    are recorded as the sentinel '<AMBIGUOUS>' so resolve_label can refuse them."""
    index: dict[str, str] = {}
    for display, aliases in cast:
        for token in [display, *aliases]:
            key = normalize_label(token)
            if not key:
                continue
            if key in index and index[key] != display:
                index[key] = "<AMBIGUOUS>"
            else:
                index.setdefault(key, display)
    return index


def resolve_label(label: str, cast_index: dict[str, str]) -> Resolution:
    hit = cast_index.get(normalize_label(label))
    if hit is None:
        return Resolution(resolved=None, ambiguous=False)
    if hit == "<AMBIGUOUS>":
        return Resolution(resolved=None, ambiguous=True)
    return Resolution(resolved=hit, ambiguous=False)
