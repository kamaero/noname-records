from __future__ import annotations

import json

from app.models import AuthorCharacter
from app.services import author_profile

_ROSTER_INSTRUCTION = (
    "ИЗВЕСТНЫЙ КАНОН АВТОРА (используй эти канонические имена и написания; объединяй "
    "указанные алиасы в одну личность; НЕ выдумывай появления персонажей, которых нет в "
    "тексте — список лишь для единообразия имён уже присутствующих):"
)


def _aliases(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
        return [str(x) for x in data] if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def build_author_roster_text(db, author_id: str) -> str:
    if not author_id:
        return ""
    rows = (
        db.query(AuthorCharacter)
        .filter(AuthorCharacter.author_id == author_id, AuthorCharacter.status == "confirmed")
        .order_by(AuthorCharacter.canonical_name.asc())
        .all()
    )
    entries: list[str] = []
    for ch in rows:
        name = (ch.canonical_name or "").strip()
        if not name:
            continue
        aliases = [a for a in _aliases(ch.aliases) if a.strip()]
        entries.append(f"{name} ({', '.join(aliases)})" if aliases else name)
    return "; ".join(entries)


def author_roster_system_suffix(db, author_id: str) -> str:
    roster = build_author_roster_text(db, author_id)
    if not roster:
        return ""
    return f"\n\n{_ROSTER_INSTRUCTION}\n{roster}"


def link_character_to_author(db, book, character) -> dict:
    result = {"matched": False, "color_set": False, "actor_set": False}
    author_id = str(getattr(book, "author_id", "") or "")
    if not author_id:
        return result
    match = author_profile.find_character(db, author_id, character.name)
    if match is None:
        return result
    result["matched"] = True
    character.author_character_id = match.id
    if not (character.character_color or "").strip() and (match.reply_color or "").strip():
        character.character_color = match.reply_color
        result["color_set"] = True
    if not (character.actor_name or "").strip() and (match.actor_name or "").strip():
        character.actor_name = match.actor_name
        result["actor_set"] = True
    return result
