"""K3: feed the verified canon roster into char_extraction for canon authors.

When a book is bound to the canon author (Белозёров), char_extraction gets the
confirmed canon roster (human-verified canonical names + aliases) so new books of
the universe are parsed with consistent naming. Sourced from the isolated
canon_kb.sqlite (built by the council + author review). Read-only, graceful if
the canon DB is absent.
"""
from __future__ import annotations

import json
import os
import sqlite3

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CANON_DB = os.path.join(_REPO, "data", "canon_kb.sqlite")
CANON_AUTHOR_SLUG = "belozerov"

_INSTRUCTION = (
    "ВЫВЕРЕННЫЙ КАНОН ВСЕЛЕННОЙ (выверен автором; используй эти канонические имена и "
    "написания; объединяй указанные алиасы в одну личность; НЕ выдумывай появления "
    "персонажей, которых нет в тексте — список лишь для единообразия имён уже присутствующих):"
)


def build_canon_roster(db_path: str = CANON_DB) -> str:
    """Confirmed (human-verified) canon characters as 'Name (alias1, alias2); …'."""
    if not os.path.exists(db_path):
        return ""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT canonical, aliases_json FROM canon_character WHERE merge_status='confirmed' ORDER BY canonical"
        ).fetchall()
    except sqlite3.Error:
        return ""
    finally:
        con.close()
    entries: list[str] = []
    for name, al in rows:
        name = (name or "").strip()
        if not name:
            continue
        try:
            aliases = [a for a in json.loads(al or "[]") if str(a).strip()]
        except (TypeError, ValueError):
            aliases = []
        entries.append(f"{name} ({', '.join(aliases)})" if aliases else name)
    return "; ".join(entries)


def _is_canon_author(db, author_id: str) -> bool:
    if not author_id:
        return False
    from app.models import Author

    a = db.query(Author).filter(Author.id == author_id).first()
    return bool(a and (a.slug or "").strip().lower() == CANON_AUTHOR_SLUG)


def canon_roster_system_suffix(db, book, db_path: str = CANON_DB) -> str:
    if not _is_canon_author(db, str(getattr(book, "author_id", "") or "")):
        return ""
    roster = build_canon_roster(db_path)
    if not roster:
        return ""
    return f"\n\n{_INSTRUCTION}\n{roster}"
