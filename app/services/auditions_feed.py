"""Раздел «Пробы» (02.10): лента проб всех книг, свежие сверху.

Строки — те же, что у листа проб в касте (`auditions.book_auditions` + реакции автора
`audition_reactions.attach_reactions` с теми же правами видимости), плюс книга и судьба
роли: кто на ней сейчас и сколько на неё проб — чтобы решать прямо из ленты.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import AudioFile, Character, ScriptBook
from app.services.audio_uploads import derive_book_code
from app.services.audition_reactions import attach_reactions
from app.services.auditions import book_auditions


def feed(db, *, viewer_name: str, sees_all: bool) -> list[dict]:
    items: list[dict] = []
    codes = {derive_book_code(str(b.title or "")) for b in db.query(ScriptBook).all()}
    with_auditions = {code for (code,) in db.query(AudioFile.book_code).filter(AudioFile.kind == "audition").distinct()}
    for book in db.query(ScriptBook).all():
        if derive_book_code(str(book.title or "")) not in with_auditions & codes:
            continue
        rows = attach_reactions(db, book_auditions(db, book_id=book.id) or [], book_id=book.id,
                                viewer_name=viewer_name, sees_all=sees_all)
        characters = {str(ch.name or ""): ch for ch in db.query(Character).filter(Character.book_id == book.id).all()}
        per_role: dict[str, int] = {}
        for row in rows:
            per_role[row["role"]] = per_role.get(row["role"], 0) + 1
        for row in rows:
            ch = characters.get(row["role"])
            row.update({
                "book_id": book.id,
                "book_title": str(book.display_title or book.title or ""),
                "character_id": ch.id if ch is not None else "",
                "role_actor": str(getattr(ch, "actor_name", "") or ""),
                "role_auditions": per_role[row["role"]],
            })
        items += rows
    items.sort(key=lambda row: row.get("uploaded_at") or "", reverse=True)
    return items


def parse_since(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def new_count(items: list[dict], since: datetime) -> int:
    marker = since.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return sum(1 for row in items if (row.get("uploaded_at") or "") > marker)
