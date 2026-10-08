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

def canon_db_path() -> str:
    """Выверенный канон — файл, который кладёт оператор; в папке данных, а не в пакете."""
    from app.paths import data_path
    return str(data_path("canon_kb.sqlite"))
CANON_AUTHOR_SLUG = "belozerov"

_INSTRUCTION = (
    "ВЫВЕРЕННЫЙ КАНОН ВСЕЛЕННОЙ (выверен автором; используй эти канонические имена и "
    "написания; объединяй указанные алиасы в одну личность; НЕ выдумывай появления "
    "персонажей, которых нет в тексте — список лишь для единообразия имён уже присутствующих):"
)


def build_canon_roster(db_path: str | None = None) -> str:
    """Confirmed (human-verified) canon characters as 'Name (alias1, alias2); …'."""
    db_path = db_path or canon_db_path()
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


def canon_roster_system_suffix(db, book, db_path: str | None = None) -> str:
    if not _is_canon_author(db, str(getattr(book, "author_id", "") or "")):
        return ""
    roster = build_canon_roster(db_path)
    if not roster:
        return ""
    return f"\n\n{_INSTRUCTION}\n{roster}"
