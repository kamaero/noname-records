"""K3+: reconcile a book's extracted characters against the canon (shadow).

After char_extraction, classify each book character as KNOWN (matches a canon
character — carry its canonical name, status and voice actor) or NEW (not yet in
the canon). Alias-aware via the same matcher used everywhere. Read-only / shadow:
this reports, it does not mutate the book. Reuses council_measure.
"""
from __future__ import annotations

from typing import Any

from app.pipeline.council import CharacterEntry
from app.pipeline.council_measure import ExistingChar, measure_proposal_vs_existing
from app.services.character_names import _normalize_character_identity_key as _key


def reconcile(book_chars: list[tuple], canon_rows: list[tuple]) -> dict[str, Any]:
    """book_chars: list of (name, aliases:list[str]).
    canon_rows: list of (canonical, aliases:list[str], merge_status, actor_name).
    Returns {known, new, confirmed:[…], new_list:[…]} with canon status+actor on knowns.
    """
    proposal = [CharacterEntry(canonical=n, aliases=list(a or [])) for n, a in book_chars]
    existing = [ExistingChar(name=c[0], aliases=list(c[1] or [])) for c in canon_rows]
    meta = {_key(c[0]): {"merge_status": c[2], "actor": c[3]} for c in canon_rows if _key(c[0])}

    r = measure_proposal_vs_existing(proposal, existing)
    for c in r["confirmed"]:
        m = meta.get(_key(c["existing"]), {})
        c["canon_status"] = m.get("merge_status", "")
        c["actor"] = m.get("actor", "")
    return {
        "known": r["confirmed_count"],
        "new": r["new_count"],
        "confirmed": r["confirmed"],
        "new_list": r["new"],
    }
