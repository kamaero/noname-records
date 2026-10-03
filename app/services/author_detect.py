"""Auto-detect the author of a book from its title/filename, to bind book.author_id.

Once a book is bound to an author, the existing author machinery activates:
the author roster seeds char_extraction (app/services/author_seed) and
char_memory_sync copies the canonical reply colour + voice actor onto matching
characters. This module only supplies the missing trigger: pick the author.

Conservative stem-aware surname match — tokens >=5 chars, prefix match — so
'Белозёровы Сказки волшебников' binds to 'Александр Белозёров' without false binds on
common words. No confident match -> "" (book stays unbound).
"""
from __future__ import annotations

import re

_MIN_TOKEN = 5


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[а-яёa-z0-9]+", str(text or "").lower()) if len(t) >= _MIN_TOKEN]


def match_author_id(text: str, authors: list[tuple[str, str]]) -> str:
    """authors: list of (author_id, name). Returns best-matching author_id or ""."""
    ttoks = _tokens(text)
    best_id, best_score = "", 0
    for aid, name in authors:
        score = 0
        for at in _tokens(name):
            for tt in ttoks:
                if at == tt or at.startswith(tt) or tt.startswith(at):
                    score += min(len(at), len(tt))
                    break
        if score > best_score:
            best_score, best_id = score, aid
    return best_id


def detect_author_id(db, *texts: str) -> str:
    """Match the given texts (title, filename, …) against known authors in the DB."""
    from app.models import Author

    authors = [(a.id, a.name) for a in db.query(Author).all() if (a.name or "").strip()]
    if not authors:
        return ""
    return match_author_id(" ".join(t for t in texts if t), authors)
