"""Жизненный цикл книги: «Глава сдана» и «Озвучена».

Сдача главы и озвучивание книги — решения человека, конвейер их не ждёт и сам не ставит.
`delivered_at` у главы (всё записано) здесь не трогаем: это другое событие.
"""
from __future__ import annotations

from datetime import datetime

from app.models import RoleDeadline, ScriptBook, ScriptChapter
from app.services import role_deadlines
from app.services.studio_settings import studio_settings
from app.time_utils import utcnow_naive


def mark_released(db, chapter_id: str, *, released: bool, now: datetime | None = None) -> ScriptChapter:
    chapter = db.get(ScriptChapter, chapter_id)
    if chapter is None:
        raise LookupError("chapter_not_found")
    chapter.released_at = (now or utcnow_naive()) if released else None
    db.flush()
    return chapter


def all_published_released(db, book_id: str) -> bool:
    """Можно предлагать «Озвучена»: опубликованные главы есть, и все сданы. Неопубликованные
    не в счёт — их ещё не размечали для записи, сдавать нечего."""
    rows = db.query(ScriptChapter.released_at).filter(
        ScriptChapter.book_id == book_id, ScriptChapter.status == "published").all()
    return bool(rows) and all(released_at is not None for (released_at,) in rows)


def is_voiced(book) -> bool:
    return getattr(book, "voiced_at", None) is not None


def voiced_book_ids(db) -> set[str]:
    return {str(book_id) for (book_id,) in db.query(ScriptBook.id).filter(ScriptBook.voiced_at.isnot(None))}


def voice_book(db, book_id: str, *, now: datetime | None = None) -> dict:
    """Объявить книгу озвученной: снять её с записи и закрыть открытые сроки ролей, иначе
    дикторам продолжили бы слать напоминания по законченной книге."""
    book = db.get(ScriptBook, book_id)
    if book is None:
        raise LookupError("book_not_found")
    now = now or utcnow_naive()
    book.voiced_at = now
    settings = studio_settings(db)
    cleared = str(settings.recording_book_id or "") == book_id
    if cleared:
        settings.recording_book_id = ""
    open_rows = db.query(RoleDeadline).filter(
        RoleDeadline.book_id == book_id, RoleDeadline.closed_at.is_(None)).all()
    for row in open_rows:
        role_deadlines._close(row, "voiced", now)
    db.flush()
    return {"voiced_at": now, "closed_deadlines": len(open_rows), "cleared_recording_book": cleared}


def reopen_book(db, book_id: str) -> None:
    """Вернуть в работу. Книгу в записи и закрытые сроки не возвращаем: что делать дальше,
    решает студия заново."""
    book = db.get(ScriptBook, book_id)
    if book is None:
        raise LookupError("book_not_found")
    book.voiced_at = None
    db.flush()
