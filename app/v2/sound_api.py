"""Ручки звуковой разметки: сцены, переходы, значимые звуки, места — для звукорежиссёра.

Только для редактора: слой правит `author`/`admin`, дикторам он не нужен и не показан
(в отличие от сценария и консилиума, где чтение открыто всем вошедшим). Ручки-обвязка
над `app.services.sound_store` (правки в базе, перепривязка при смене текста) и
`app.services.sound_engine` (смета, прогон, деньги) — по образцу ручек консилиума
в `app.v2.api` (`api_v2_consilium*`).
"""
from __future__ import annotations

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api._spend_gate import limit_gate, with_warning
from app.db import SessionLocal
from app.services import spend
from app.models import ScriptBook, ScriptChapter
from app.v2.api import (
    _can_edit,
    _is_authenticated,
    _json_body_or_empty,
    bad_request_response,
    error_response,
    forbidden_response,
    not_found_response,
    unauthorized_response,
)


def _sound_error(code: str) -> JSONResponse:
    """Коды ошибок хранилища → ответ: `not_found` — 404, остальное (`bad_*`) — 400."""
    if code == "not_found":
        return not_found_response()
    return bad_request_response(code)


def _sound_blocked(db, book, *, credits, estimate_rub) -> str:
    """Копия `_consilium_blocked`, но по своей очереди — `sound:{book_id}`.

    Прогон консилиума на той же книге не мешает: они делят одну очередь воркера и
    сериализуются в ней сами, а кнопка озвучки не обязана ждать чужого прогона.
    """
    from app.models import BackgroundRun
    from app.services.consilium_engine import BUSY_STATUSES, MIN_CREDITS_RUB, expire_dead_runs

    expire_dead_runs(db, book.id, kind="sound")
    running = (db.query(BackgroundRun)
               .filter(BackgroundRun.run_key == f"sound:{book.id}",
                       BackgroundRun.status.in_(("queued", "running"))).first())
    if running is not None:
        return "already_running"
    if str(book.status or "") in BUSY_STATUSES:
        return "book_busy"
    if credits is not None and credits < max(MIN_CREDITS_RUB, estimate_rub):
        return "no_credits"
    return ""


def api_v2_sound_chapter(request: Request, chapter_id: str):
    """`GET /api/v2/chapters/{id}/sound` — маркеры главы: список, потерянные, места."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services.sound_store import chapter_markers, reanchor_chapter, touch_sessions

    with SessionLocal() as db:
        chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
        if chapter is None:
            return not_found_response("chapter_not_found")
        # Перепривязка отдельно от чтения: она может сдвинуть или потерять маркер —
        # архивный проект главы (если есть) с этого момента ему не соответствует.
        # `chapter_markers` ниже зовёт `reanchor_chapter` тоже (для не-первого чтения
        # это второй проход впустую — текст уже привязан, двигать нечего), но сама
        # она об этом молчит, а без своего вызова здесь узнать про переезд нечем.
        moved = reanchor_chapter(db, chapter.id)
        if moved:
            touch_sessions(db, [chapter.id])
        payload = chapter_markers(db, chapter.id)
        db.commit()
    return JSONResponse({"ok": True, **payload})


async def api_v2_sound_marker_add(request: Request, chapter_id: str):
    """`POST /api/v2/chapters/{id}/sound/markers` — ручная сцена/переход/звук."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    body = await _json_body_or_empty(request)
    if body is None:
        return bad_request_response()
    from app.services.sound_store import _view, add_marker, touch_sessions

    with SessionLocal() as db:
        result = add_marker(db, chapter_id=str(chapter_id or "").strip(),
                            segment_id=str(body.get("segment_id") or ""),
                            kind=str(body.get("kind") or ""), fields=body)
        if isinstance(result, str):
            db.rollback()
            return _sound_error(result)
        touch_sessions(db, [result.chapter_id])
        db.commit()
        marker = _view(result)
    return JSONResponse({"ok": True, "marker": marker})


async def api_v2_sound_marker_edit(request: Request, marker_id: str):
    """`PATCH /api/v2/sound/markers/{id}` — правка полей, перенос на другой абзац."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    body = await _json_body_or_empty(request)
    if body is None:
        return bad_request_response()
    from app.services.sound_store import _view, edit_marker, touch_sessions

    with SessionLocal() as db:
        result = edit_marker(db, str(marker_id or "").strip(), body)
        if isinstance(result, str):
            # На отказе (например «bad_place») строка могла остаться гряз­ной —
            # откатываем, а не фиксируем половину правки.
            db.rollback()
            return _sound_error(result)
        touch_sessions(db, [result.chapter_id])
        db.commit()
        marker = _view(result)
    return JSONResponse({"ok": True, "marker": marker})


def api_v2_sound_marker_dismiss(request: Request, marker_id: str):
    """`DELETE /api/v2/sound/markers/{id}` — отклонить маркер (не воскресает прогоном)."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.models import SoundMarker
    from app.services.sound_store import dismiss_marker, touch_sessions

    with SessionLocal() as db:
        clean_id = str(marker_id or "").strip()
        # Чей отказ — узнаём до `dismiss_marker`: строка не удаляется, только меняет
        # статус, но заводить в помощнике второй способ узнать главу незачем.
        row = db.get(SoundMarker, clean_id)
        ok = dismiss_marker(db, clean_id)
        if not ok:
            db.rollback()
            return not_found_response()
        touch_sessions(db, [row.chapter_id] if row is not None else [])
        db.commit()
    return JSONResponse({"ok": True})


def api_v2_sound_book(request: Request, book_id: str):
    """`GET /api/v2/books/{id}/sound` — растущий список мест книги, пары на решение, прогон."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services.consilium_engine import latest_run
    from app.services.sound_store import pair_rows, place_views

    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        payload = {"places": place_views(db, book.id), "pairs": pair_rows(db, book.id),
                  "run": latest_run(db, book.id, kind="sound")}
        db.commit()  # мёртвый прогон, отмеченный при чтении run'а, отмечается и в базе
    return JSONResponse({"ok": True, **payload})


async def api_v2_sound_place_edit(request: Request, place_id: str):
    """`PATCH /api/v2/sound/places/{id}` — имя, описание, запросы подложки."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    body = await _json_body_or_empty(request)
    if body is None:
        return bad_request_response()
    from app.models import SoundMarker
    from app.services.sound_store import edit_place, touch_sessions

    with SessionLocal() as db:
        result = edit_place(db, str(place_id or "").strip(), body)
        if result is None:
            db.rollback()
            return not_found_response()
        if isinstance(result, str):
            db.rollback()
            return _sound_error(result)
        # Все главы, где место сейчас звучит в активном маркере — не только та,
        # с которой открыли карточку: место одно на книгу, глав у него может быть много.
        chapter_ids = [
            chapter_id for (chapter_id,) in
            db.query(SoundMarker.chapter_id)
              .filter(SoundMarker.place_id == result.id, SoundMarker.status == "active")
              .distinct()
        ]
        touch_sessions(db, chapter_ids)
        db.commit()
    return JSONResponse({"ok": True})


async def api_v2_sound_pair_decide(request: Request, pair_id: str):
    """`POST /api/v2/sound/pairs/{id}` — решить кандидата на склейку мест: слить или оставить порознь."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    body = await _json_body_or_empty(request)
    if body is None:
        return bad_request_response()
    merge = body.get("merge")
    if not isinstance(merge, bool):
        # Строка "false" не должна сливать, а пропущенный ключ — не «оставить порознь»:
        # оба молча превращались бы в конкретное решение, которого никто не просил.
        return bad_request_response("bad_merge")
    from app.models import SoundMarker, SoundPlacePair
    from app.services.sound_store import decide_pair, touch_sessions

    with SessionLocal() as db:
        clean_id = str(pair_id or "").strip()
        result = decide_pair(db, clean_id, merge)
        if result == "not_found":
            db.rollback()
            return not_found_response()
        if result == "merged":
            # `decide_pair` уже перевесил маркеры склеенного места на оставшееся
            # (`place_a`) — его активные маркеры теперь покрывают главы ОБЕИХ карточек.
            pair = db.get(SoundPlacePair, clean_id)
            chapter_ids = [
                chapter_id for (chapter_id,) in
                db.query(SoundMarker.chapter_id)
                  .filter(SoundMarker.place_id == pair.place_a, SoundMarker.status == "active")
                  .distinct()
            ]
            touch_sessions(db, chapter_ids)
        db.commit()
    return JSONResponse({"ok": True, "status": result})


async def api_v2_sound_estimate(request: Request, book_id: str):
    """`GET /api/v2/books/{id}/sound/estimate` — смета, баланс, заблокированность кнопок."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services import sound_engine

    # Смета книги — секунды базы; на цикле событий она морозила бы весь сайт.
    def plan_and_check():
        with SessionLocal() as db:
            book = db.get(ScriptBook, str(book_id or "").strip())
            if book is None:
                return None, None, None, None
            plan = sound_engine.estimate(db, book.id)
            credits = sound_engine.read_credits()
            blocked = {mode: _sound_blocked(db, book, credits=credits, estimate_rub=plan["estimate_rub"][mode])
                       for mode in ("rest", "all")}
            db.commit()
            return book, plan, credits, blocked

    book, plan, credits, blocked = await asyncio.to_thread(plan_and_check)
    if book is None:
        return not_found_response("book_not_found")
    return JSONResponse({"ok": True, **plan, "credits": credits, "blocked": blocked})


async def api_v2_sound_run(request: Request, book_id: str):
    """`POST /api/v2/books/{id}/sound/run` — запустить чтение книги (`rest`/`all`)."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    body = await _json_body_or_empty(request)
    mode = str((body or {}).get("mode") or "")
    if mode not in ("rest", "all"):
        return bad_request_response("bad_mode")
    from app.services import sound_engine

    def plan_and_check():
        with SessionLocal() as db:
            book = db.get(ScriptBook, str(book_id or "").strip())
            if book is None:
                return None, None, ""
            plan = sound_engine.estimate(db, book.id)
            if plan["chapters_total"] == 0:
                return book.id, plan, ""
            credits = sound_engine.read_credits()
            blocked = _sound_blocked(db, book, credits=credits, estimate_rub=plan["estimate_rub"][mode])
            gate = None if blocked else limit_gate(db, request, estimate_rub=plan["estimate_rub"][mode],
                                                   unknown_price=spend.unknown_for(db, ("sound",)), what="звуковая разметка")
            db.commit()
            return book.id, plan, blocked or gate

    found_id, plan, blocked = await asyncio.to_thread(plan_and_check)
    if isinstance(blocked, JSONResponse):
        return blocked
    if found_id is None:
        return not_found_response("book_not_found")
    if plan["chapters_total"] == 0:
        return bad_request_response("no_markup")
    if blocked in ("already_running", "book_busy"):
        return error_response(blocked, status_code=409)
    if blocked:
        return bad_request_response(blocked)
    run_id = sound_engine.enqueue_sound(found_id, mode)
    if not run_id:
        return error_response("already_running", status_code=409)
    return JSONResponse(with_warning(request, {"ok": True, "run_id": run_id}))


async def api_v2_sound_chapter_run(request: Request, chapter_id: str):
    """`POST /api/v2/chapters/{id}/sound/run` — перечитать одну главу (`mode="chapter"`)."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services import sound_engine

    def plan_and_check():
        with SessionLocal() as db:
            chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
            if chapter is None:
                return None, None, None, ""
            book = db.get(ScriptBook, chapter.book_id)
            if book is None:
                return None, None, None, ""
            plan = sound_engine.estimate(db, book.id, chapter_id=chapter.id)
            credits = sound_engine.read_credits()
            blocked = _sound_blocked(db, book, credits=credits, estimate_rub=plan["estimate_rub"]["all"])
            gate = None if blocked else limit_gate(db, request, estimate_rub=plan["estimate_rub"]["all"],
                                                   unknown_price=spend.unknown_for(db, ("sound",)), what="звуковая разметка главы")
            db.commit()
            return book.id, chapter.id, plan, blocked or gate

    found_book_id, found_chapter_id, plan, blocked = await asyncio.to_thread(plan_and_check)
    if isinstance(blocked, JSONResponse):
        return blocked
    if found_book_id is None:
        return not_found_response("chapter_not_found")
    if plan["chapters_total"] == 0:
        return bad_request_response("no_markup")
    if blocked in ("already_running", "book_busy"):
        return error_response(blocked, status_code=409)
    if blocked:
        return bad_request_response(blocked)
    run_id = sound_engine.enqueue_sound(found_book_id, "chapter", chapter_id=found_chapter_id)
    if not run_id:
        return error_response("already_running", status_code=409)
    return JSONResponse(with_warning(request, {"ok": True, "run_id": run_id}))


def api_v2_sound_stop(request: Request, book_id: str):
    """`POST /api/v2/books/{id}/sound/stop` — попросить прогон остановиться на ближайшей главе."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services.consilium_engine import request_stop

    with SessionLocal() as db:
        stopped = request_stop(db, str(book_id or "").strip(), kind="sound")
        db.commit()
    return JSONResponse({"ok": True, "stopped": stopped})
