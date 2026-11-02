"""Раздел «Пробы»: пары «актёр + роль» всех книг, свежие сверху.

Строки — те же пары, что у листа проб в касте (`audition_pairs.book_pairs`, те же права
видимости). Счётчик «новых» в меню считает файлы, а не пары: новая проба старого
актёра — тоже новость.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import AudioFile, ScriptBook
from app.services.audio_uploads import derive_book_code
from app.services.audition_pairs import book_pairs


def feed(db, *, viewer_name: str, sees_scores: bool) -> list[dict]:
    with_auditions = {code for (code,) in db.query(AudioFile.book_code).filter(AudioFile.kind == "audition").distinct()}
    items: list[dict] = []
    for book in db.query(ScriptBook).all():
        if derive_book_code(str(book.title or "")) not in with_auditions:
            continue
        items += book_pairs(db, book, sees_scores=sees_scores, viewer_name=viewer_name)
    items.sort(key=lambda pair: pair.get("uploaded_at") or "", reverse=True)
    return items


def parse_since(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(raw or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(timezone.utc).replace(tzinfo=None) if moment.tzinfo else moment


def new_count(items: list[dict], since: datetime) -> int:
    marker = since.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
    return sum(1 for pair in items for item in pair["auditions"] if (item.get("uploaded_at") or "") > marker)
