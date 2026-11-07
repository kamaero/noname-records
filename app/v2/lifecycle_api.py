"""Ручки жизненного цикла книги: «Глава сдана» и «Озвучена». Только админ.

Подключаются в `app.main` напрямую, мимо реестра обработчиков, как `app.api.payouts`.
"""
from __future__ import annotations

from fastapi import Body, Request
from fastapi.responses import JSONResponse

from app.auth import has_any_role, is_authenticated
from app.db import SessionLocal
from app.models import ScriptBook
from app.services import book_lifecycle, book_thanks
from app.time_utils import iso_utc


def _refused(request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    if not has_any_role(request, {"admin"}):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return None


def _flag(body, key: str) -> bool:
    return bool(body.get(key)) if isinstance(body, dict) else False


def api_v2_chapter_released(chapter_id: str, request: Request, body: dict = Body(default_factory=dict)):
    if (refused := _refused(request)) is not None:
        return refused
    with SessionLocal() as db:
        try:
            chapter = book_lifecycle.mark_released(db, chapter_id, released=_flag(body, "released"))
        except LookupError:
            return JSONResponse({"ok": False, "error": "chapter_not_found"}, status_code=404)
        book = db.get(ScriptBook, chapter.book_id)
        payload = {
            "ok": True, "chapter_id": chapter.id, "released_at": iso_utc(chapter.released_at) or "",
            "book_id": chapter.book_id,
            "all_released": book_lifecycle.all_published_released(db, chapter.book_id),
            "voiced": book_lifecycle.is_voiced(book),
        }
        db.commit()
    return payload


def api_v2_book_voiced(book_id: str, request: Request, body: dict = Body(default_factory=dict)):
    if (refused := _refused(request)) is not None:
        return refused
    with SessionLocal() as db:
        try:
            if _flag(body, "voiced"):
                result = book_lifecycle.voice_book(db, book_id)
            else:
                book_lifecycle.reopen_book(db, book_id)
                result = {"voiced_at": None, "closed_deadlines": 0, "cleared_recording_book": False}
        except LookupError:
            return JSONResponse({"ok": False, "error": "book_not_found"}, status_code=404)
        db.commit()
    return {"ok": True, "voiced_at": iso_utc(result["voiced_at"]) or "",
            "closed_deadlines": result["closed_deadlines"],
            "cleared_recording_book": result["cleared_recording_book"]}


def _voiced_book(db, book_id: str):
    book = db.get(ScriptBook, book_id)
    if book is None:
        return None, JSONResponse({"ok": False, "error": "book_not_found"}, status_code=404)
    if not book_lifecycle.is_voiced(book):
        return None, JSONResponse({"ok": False, "error": "book_not_voiced"}, status_code=409)
    return book, None


def api_v2_book_thanks(book_id: str, request: Request):
    if (refused := _refused(request)) is not None:
        return refused
    with SessionLocal() as db:
        book, error = _voiced_book(db, book_id)
        if error is not None:
            return error
        people = book_thanks.recipients(db, book_id)
        sample = people["people"][0] if people["people"] else {"actor": "Тестова Лада", "roles": ["Учебный персонаж"]}
        preview = book_thanks.render(book_thanks.DEFAULT_TEXT, name=book_thanks.first_name(sample["actor"]),
                                     roles=sample["roles"], book=str(book.display_title or book.title or ""))
    return {"ok": True, "text": book_thanks.DEFAULT_TEXT, "preview": preview, **people}


def api_v2_book_thanks_send(book_id: str, request: Request, body: dict = Body(default_factory=dict)):
    if (refused := _refused(request)) is not None:
        return refused
    text = str(body.get("text") or "").strip() if isinstance(body, dict) else ""
    if not text:
        return JSONResponse({"ok": False, "error": "empty_text"}, status_code=400)
    with SessionLocal() as db:
        _, error = _voiced_book(db, book_id)
        if error is not None:
            return error
        result = book_thanks.send(db, book_id, text)
    return {"ok": True, **result}


def register_lifecycle_routes(app) -> None:
    app.add_api_route("/api/v2/chapters/{chapter_id}/released", api_v2_chapter_released, methods=["POST"])
    app.add_api_route("/api/v2/books/{book_id}/voiced", api_v2_book_voiced, methods=["POST"])
    app.add_api_route("/api/v2/books/{book_id}/thanks", api_v2_book_thanks, methods=["GET"])
    app.add_api_route("/api/v2/books/{book_id}/thanks", api_v2_book_thanks_send, methods=["POST"])
