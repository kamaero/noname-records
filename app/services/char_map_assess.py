"""Clean/dirty assessment of a book's extracted character map (read-only).

Classifies the freshly-extracted map after char_extraction so the pipeline can
decide whether to auto-continue (clean) or pause for human review (dirty). Canon
books (Белозёров) get known/new from canon_reconcile; others degrade gracefully
(canon_available=False — known/new are 0, only structural signals apply). No
writes — this only reports.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.services.character_names import _normalize_character_aliases as _aliases
from app.services.canon_reconcile import reconcile
from app.services.canon_seed import _is_canon_author
from app.services.canon_seed import canon_db_path as _canon_db_path


@dataclass
class CharMapAssessment:
    version: int
    status: str  # "clean" | "dirty"
    total: int
    known: int
    new: int
    ambiguous: int
    blocking_reasons: list[dict[str, Any]] = field(default_factory=list)
    canon_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": self.status,
            "total": self.total,
            "known": self.known,
            "new": self.new,
            "ambiguous": self.ambiguous,
            "blocking_reasons": self.blocking_reasons,
            "canon_available": self.canon_available,
        }


def _load_canon_rows(db_path: str) -> list[tuple]:
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)  # canon_kb is read-only (isolation)
    try:
        return [
            (r[0], json.loads(r[1] or "[]"), r[2], r[3] or "")
            for r in con.execute(
                "SELECT canonical, aliases_json, merge_status, COALESCE(actor_name,'') FROM canon_character"
            ).fetchall()
        ]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def assess_char_map(db, book, *, canon_db_path: str | None = None) -> CharMapAssessment:
    from app.models import Character

    chars = (
        db.query(Character)
        .filter(Character.book_id == book.id)
        .order_by(Character.name.asc())
        .all()
    )
    total = len(chars)
    version = int(getattr(book, "char_map_version", 0) or 0)

    suspect = settings.char_map_suspect_aliases
    ambiguous = sum(1 for c in chars if len(_aliases(c.aliases)) >= suspect)

    canon_available = _is_canon_author(db, str(getattr(book, "author_id", "") or ""))
    known = new = 0
    if canon_available:
        canon_rows = _load_canon_rows(canon_db_path or _canon_db_path())
        canon_available = bool(canon_rows)
        if canon_available:
            book_chars = [(c.name, _aliases(c.aliases)) for c in chars]
            r = reconcile(book_chars, canon_rows)
            known, new = int(r["known"]), int(r["new"])

    blocking: list[dict[str, Any]] = []
    if ambiguous >= 1:
        blocking.append({"code": "AMBIGUOUS_MERGE", "count": ambiguous})

    new_ratio = (new / total) if (canon_available and total) else 0.0
    if canon_available and new_ratio > settings.char_map_new_ratio_gate:
        blocking.append({
            "code": "NEW_RATIO_HIGH",
            "value": round(new_ratio, 3),
            "threshold": settings.char_map_new_ratio_gate,
        })

    status = "dirty" if blocking else "clean"
    return CharMapAssessment(
        version=version, status=status, total=total, known=known, new=new,
        ambiguous=ambiguous, blocking_reasons=blocking, canon_available=canon_available,
    )
