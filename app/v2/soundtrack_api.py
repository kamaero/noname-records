"""Редактор сохраняет выбор; каталог и экспорт не меняют звук и проекты главы."""
from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db import SessionLocal
from app.services.openverse_client import OpenverseError, client as openverse
from app.services.soundtrack_store import SoundtrackError, chapter_for, chapter_payload, credits_text, mutate
from app.v2.api import _can_edit, _is_authenticated, _json_body_or_empty
from app.v2.api import bad_request_response, forbidden_response, unauthorized_response


def _access(request):
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    return None


def _error(exc):
    if isinstance(exc, (SoundtrackError, OpenverseError)):
        headers = {}
        if isinstance(exc, OpenverseError) and exc.retry_after:
            headers["Retry-After"] = exc.retry_after
        return JSONResponse({"ok": False, "error": exc.code}, status_code=exc.status, headers=headers)
    if isinstance(exc, (IntegrityError, OperationalError)):
        return JSONResponse({"ok": False, "error": "version_conflict"}, status_code=409)
    return bad_request_response(str(exc))


def api_v2_soundtrack_chapter(request: Request, chapter_id: str):
    denied = _access(request)
    if denied is not None:
        return denied
    try:
        with SessionLocal() as db:
            return JSONResponse(chapter_payload(db, chapter_id))
    except SoundtrackError as exc:
        return _error(exc)


def _mutation(chapter_id, body):
    try:
        with SessionLocal() as db:
            response = mutate(db, chapter_id, body, openverse_detail=openverse.detail)
            db.commit()
            return JSONResponse(response)
    except (ValueError, OpenverseError, IntegrityError, OperationalError) as exc:
        return _error(exc)


async def api_v2_soundtrack_mutate(request: Request, chapter_id: str):
    denied = _access(request)
    if denied is not None:
        return denied
    body = await _json_body_or_empty(request)
    if not isinstance(body, dict):
        return bad_request_response()
    return await asyncio.to_thread(_mutation, chapter_id, body)


def api_v2_soundtrack_search(request: Request, chapter_id: str):
    denied = _access(request)
    if denied is not None:
        return denied
    try:
        with SessionLocal() as db:
            chapter_for(db, chapter_id)
        page = request.query_params.get("page", "1")
        if not page.isdigit():
            raise ValueError("bad_page")
        payload = openverse.search(request.query_params.get("q", ""), int(page),
                                   request.query_params.get("layer", "music"))
        return JSONResponse({"ok": True, **payload})
    except (ValueError, OpenverseError) as exc:
        return _error(exc)


def api_v2_soundtrack_credits(request: Request, chapter_id: str):
    denied = _access(request)
    if denied is not None:
        return denied
    try:
        with SessionLocal() as db:
            text = credits_text(db, chapter_id)
        return Response(text.encode("utf-8"), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="soundtrack-credits.txt"'})
    except SoundtrackError as exc:
        return _error(exc)
