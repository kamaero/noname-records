"""Ф1b: alias-aware measure of a council proposal against existing char_memory.

The key Ф1a lesson: exact-name comparison wildly overstates divergence (casing,
canonical-form, alias differences). This module matches using the SAME identity-key
normalization the rest of the system uses for character matching, so the measure
predicts how a controlled apply (Ф1c) would reconcile.

Pure — no DB. The DB→inputs adapter lives in the measure script.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Reuse the system's identity normalization so the measure matches the same way
# apply-time reconciliation does (_find_matching_character in char_memory_sync).
from app.services.character_names import _normalize_character_identity_key


@dataclass
class ExistingChar:
    name: str
    aliases: list[str] = field(default_factory=list)


def _keys(name: str, aliases: list[str]) -> set[str]:
    return {
        k
        for k in (_normalize_character_identity_key(x) for x in [name, *aliases])
        if k
    }


def measure_proposal_vs_existing(proposal_chars: list[Any], existing_chars: list[ExistingChar]) -> dict[str, Any]:
    """Classify each proposal character against the existing cast.

    proposal_chars: objects with .canonical, .aliases, .appears_in (e.g. CharacterEntry).
    Returns confirmed / new / missing classification + alias-gain, all alias-aware.
    """
    existing_keysets = [(_keys(e.name, e.aliases), e) for e in existing_chars]
    matched_idx: set[int] = set()
    confirmed: list[dict[str, Any]] = []
    new: list[dict[str, Any]] = []

    for pc in proposal_chars:
        p_aliases = list(getattr(pc, "aliases", []) or [])
        p_keys = _keys(pc.canonical, p_aliases)
        match_i = None
        for i, (e_keys, _e) in enumerate(existing_keysets):
            if p_keys & e_keys:
                match_i = i
                break
        if match_i is None:
            new.append(
                {
                    "canonical": pc.canonical,
                    "aliases": p_aliases,
                    "appears_in": list(getattr(pc, "appears_in", []) or []),
                }
            )
            continue
        matched_idx.add(match_i)
        e = existing_keysets[match_i][1]
        existing_alias_keys = _keys(e.name, e.aliases)
        alias_gain = [a for a in p_aliases if _normalize_character_identity_key(a) not in existing_alias_keys]
        confirmed.append(
            {
                "proposal": pc.canonical,
                "existing": e.name,
                "alias_gain": alias_gain,
                "appears_in": list(getattr(pc, "appears_in", []) or []),
            }
        )

    missing = [existing_chars[i].name for i in range(len(existing_chars)) if i not in matched_idx]
    return {
        "confirmed_count": len(confirmed),
        "new_count": len(new),
        "missing_count": len(missing),
        "alias_gain_total": sum(len(c["alias_gain"]) for c in confirmed),
        "confirmed": confirmed,
        "new": new,
        "missing": missing,
    }
