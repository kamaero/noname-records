"""/api/dictors — раздел «Дикторы». Логика — `app/services/dictors.py`, здесь права и ответы.

Видят и правят заметку админ и автор; заводят, переименовывают, удаляют и правят демо —
только админ (спека 2026-09-30, решение 4). Подключено напрямую в `app/main.py`, мимо
реестра обработчиков — как маршруты v2.
"""
import os
import tempfile

from fastapi import File, Form, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response

from app.api._helpers import forbidden_response, not_found_response, unauthorized_response
from app.auth import has_any_role, is_authenticated, is_owner_telegram, session_payload, session_roles
from app.db import SessionLocal
from app.services import dictors
from app.services.audio_storage import file_size, read_range, resolve_path
from app.services.auditions import inline_disposition, slice_for_range
from app.services.dictors import DictorError
from app.services.onboarding import bot_reachable
from app.services.telegram import send_telegram_message

_NOT_FOUND = {"not_found", "demo_not_found"}
_CONFLICT = {"env_whitelist", "has_roles", "name_taken", "duplicate", "telegram_taken", "login_taken", "last_admin", "self_delete"}


def _viewer(request: Request) -> bool:
    return has_any_role(request, {"author"}) or is_owner_telegram(request)


def _admin(request: Request) -> bool:
    return "admin" in session_roles(request) or is_owner_telegram(request)


def _gate(request: Request, *, admin: bool = False):
    if not is_authenticated(request):
        return unauthorized_response()
    if not (_admin(request) if admin else _viewer(request)):
        return forbidden_response()
    return None


def _refusal(error: DictorError) -> JSONResponse:
    code = (status.HTTP_404_NOT_FOUND if error.code in _NOT_FOUND
            else status.HTTP_409_CONFLICT if error.code in _CONFLICT else status.HTTP_400_BAD_REQUEST)
    return JSONResponse({"ok": False, "error": error.code, "detail": error.detail}, status_code=code)


def _reach(telegram_user_id: str):
    return bot_reachable(telegram_user_id) if telegram_user_id else None


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — пустое или не JSON тело: поля просто не пришли
        return {}
    return body if isinstance(body, dict) else {}


def api_dictors_list(request: Request):
    if (refused := _gate(request)) is not None:
        return refused
    with SessionLocal() as db:
        items = dictors.list_dictors(db)
    for item in items:
        item["reachable"] = _reach(item["telegram_user_id"])
    return JSONResponse({"ok": True, "items": items})


def api_dictors_picker(request: Request):
    """Список для выбора актёра в касте. Агент (кастинг-директор) видит имена и «на связи»;
    демо и заметки — только админ и автор, как и весь раздел (спека, решение 4)."""
    from app.auth import is_agent

    if not is_authenticated(request):
        return unauthorized_response()
    can_listen = _viewer(request)
    if not (can_listen or is_agent(request)):
        return forbidden_response()
    with SessionLocal() as db:
        items = dictors.list_dictors(db)
    out = []
    for item in items:
        row = {"user_id": item["user_id"], "name": item["name"], "reachable": _reach(item["telegram_user_id"]),
               "has_telegram": bool(item["telegram_user_id"]), "roles": item["roles"]}
        row.update({"main_demo": item["main_demo"], "demos": item["demos"], "note": item["note"]} if can_listen
                   else {"main_demo": None, "demos": 0, "note": ""})
        out.append(row)
    return JSONResponse({"ok": True, "items": out, "can_listen": can_listen})


def api_dictor_card(request: Request, user_id: str):
    if (refused := _gate(request)) is not None:
        return refused
    with SessionLocal() as db:
        try:
            card = dictors.dictor_card(db, user_id)
        except DictorError as error:
            return _refusal(error)
    return JSONResponse({"ok": True, **card, "reachable": _reach(card["telegram_user_id"])})


async def api_dictor_note(request: Request, user_id: str):
    if (refused := _gate(request)) is not None:
        return refused
    body = await _json(request)
    with SessionLocal() as db:
        try:
            dictors.set_note(db, user_id, str(body.get("note") or ""))
        except DictorError as error:
            return _refusal(error)
        db.commit()
    return JSONResponse({"ok": True})


async def api_dictor_create(request: Request):
    if (refused := _gate(request, admin=True)) is not None:
        return refused
    body = await _json(request)
    with SessionLocal() as db:
        try:
            made = dictors.create_dictor(db, str(body.get("name") or ""), str(body.get("telegram_user_id") or ""),
                                         str(body.get("username") or ""))
        except DictorError as error:
            return _refusal(error)
        db.commit()
    return JSONResponse({"ok": True, **made})


async def api_dictor_rename(request: Request, user_id: str):
    if (refused := _gate(request, admin=True)) is not None:
        return refused
    body = await _json(request)
    with SessionLocal() as db:
        try:
            report = dictors.rename_dictor(db, user_id, str(body.get("name") or ""))
            db.commit()
        except DictorError as error:
            db.rollback()
            return _refusal(error)
        except Exception:
            # Явный rollback: только он возвращает файлы, переехавшие при переименовании
            # (`audio_rename` слушает after_rollback; закрытие сессии их не вернёт).
            db.rollback()
            raise
    return JSONResponse({"ok": True, **report})


def api_dictor_delete(request: Request, user_id: str):
    if (refused := _gate(request, admin=True)) is not None:
        return refused
    actor = str(session_payload(request).get("uid") or "")
    with SessionLocal() as db:
        try:
            dictors.delete_dictor(db, user_id, actor_user_id=actor)
        except DictorError as error:
            return _refusal(error)
        db.commit()
    return JSONResponse({"ok": True})


async def api_dictor_main_demo(request: Request, user_id: str):
    if (refused := _gate(request, admin=True)) is not None:
        return refused
    body = await _json(request)
    with SessionLocal() as db:
        try:
            dictors.set_main_demo(db, user_id, str(body.get("demo_id") or ""))
        except DictorError as error:
            return _refusal(error)
        db.commit()
    return JSONResponse({"ok": True})


def api_dictor_demo_delete(request: Request, demo_id: str):
    if (refused := _gate(request, admin=True)) is not None:
        return refused
    with SessionLocal() as db:
        try:
            dictors.delete_demo(db, demo_id)
        except DictorError as error:
            return _refusal(error)
        db.commit()
    return JSONResponse({"ok": True})


def api_dictor_demo_upload(request: Request, user_id: str, file: UploadFile = File(...), title: str = Form("")):
    """Любое аудио или видео; в демо уходят первые две минуты, MP3 192 кбит/с (ffmpeg)."""
    if (refused := _gate(request, admin=True)) is not None:
        return refused
    suffix = os.path.splitext(str(file.filename or ""))[1][:10]
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, f"source{suffix}")
        with open(source, "wb") as handle:
            while chunk := file.file.read(1024 * 1024):
                handle.write(chunk)
        default_title = os.path.splitext(str(file.filename or ""))[0]
        with SessionLocal() as db:
            try:
                demo = dictors.add_demo(db, user_id, source, title or default_title)
            except DictorError as error:
                return _refusal(error)
            except Exception as exc:  # noqa: BLE001 — ffmpeg не разобрал файл: отказ, а не 500
                return JSONResponse({"ok": False, "error": "not_media", "detail": type(exc).__name__},
                                    status_code=status.HTTP_400_BAD_REQUEST)
            db.commit()
    return JSONResponse({"ok": True, "demo": demo})


def api_dictor_demo_audio(request: Request, demo_id: str):
    """MP3 демо с поддержкой `Range` — иначе у плеера не двигается ползунок."""
    if (refused := _gate(request)) is not None:
        return refused
    from app.models import DictorDemo

    with SessionLocal() as db:
        demo = db.get(DictorDemo, str(demo_id or ""))
        if demo is None:
            return not_found_response("demo_not_found")
        key, name = str(demo.stored_key or ""), f"{demo.title or 'демо'}.mp3"
    try:
        total = file_size(key)
    except OSError:
        return not_found_response("audio_file_missing")
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": inline_disposition(name)}
    window = slice_for_range(request.headers.get("range") or "", total)
    if window is None:
        return FileResponse(resolve_path(key), media_type="audio/mpeg", headers=headers)
    start, end = window
    headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return Response(content=read_range(key, start, end - start + 1), media_type="audio/mpeg",
                    status_code=206, headers=headers)


async def api_deadline_extend(request: Request, deadline_id: str):
    """Продлить срок — только админ (решение владельца 01.10). Диктору — письмо с новой датой."""
    from app.auth import session_display_name
    from app.models import Character, ScriptBook
    from app.services import role_deadlines

    if (refused := _gate(request, admin=True)) is not None:
        return refused
    body = await _json(request)
    with SessionLocal() as db:
        try:
            row = role_deadlines.extend(db, deadline_id, days=body.get("days"), until=str(body.get("date") or ""),
                                        by=session_display_name(request) or "")
        except role_deadlines.DeadlineError as error:
            status_code = status.HTTP_404_NOT_FOUND if error.code == "not_found" else status.HTTP_400_BAD_REQUEST
            return JSONResponse({"ok": False, "error": error.code}, status_code=status_code)
        except (TypeError, ValueError):
            return JSONResponse({"ok": False, "error": "bad_days"}, status_code=status.HTTP_400_BAD_REQUEST)
        db.commit()
        character = db.get(Character, row.character_id)
        book = db.get(ScriptBook, row.book_id)
        chat = role_deadlines.chat_for(db, row.actor_name)  # только однозначному адресату
        notified = False
        if chat:
            text = role_deadlines.extension_notice(row, role=str(getattr(character, "name", "") or ""),
                                                   book_title=str(getattr(book, "title", "") or ""))
            notified = bool(send_telegram_message(db, text, chat_ids=[chat], direct=True))
        view = role_deadlines.deadline_view(db, row)
    return JSONResponse({"ok": True, "deadline": view, "notified": notified})


def api_deadline_close(request: Request, deadline_id: str):
    from app.services import role_deadlines

    if (refused := _gate(request, admin=True)) is not None:
        return refused
    with SessionLocal() as db:
        try:
            role_deadlines.close_manual(db, deadline_id)
        except role_deadlines.DeadlineError:
            return not_found_response("deadline_not_found")
        db.commit()
    return JSONResponse({"ok": True})


def register_dictors_routes(app) -> None:
    # Порядок важен: /api/dictors/demos/... раньше /api/dictors/{user_id}.
    app.add_api_route("/api/dictors/demos/{demo_id}/audio", api_dictor_demo_audio, methods=["GET"])
    app.add_api_route("/api/dictors/demos/{demo_id}", api_dictor_demo_delete, methods=["DELETE"])
    app.add_api_route("/api/dictors/picker", api_dictors_picker, methods=["GET"])
    app.add_api_route("/api/dictors", api_dictors_list, methods=["GET"])
    app.add_api_route("/api/dictors", api_dictor_create, methods=["POST"])
    app.add_api_route("/api/dictors/{user_id}", api_dictor_card, methods=["GET"])
    app.add_api_route("/api/dictors/{user_id}", api_dictor_rename, methods=["PATCH"])
    app.add_api_route("/api/dictors/{user_id}", api_dictor_delete, methods=["DELETE"])
    app.add_api_route("/api/dictors/{user_id}/note", api_dictor_note, methods=["PATCH"])
    app.add_api_route("/api/dictors/{user_id}/main-demo", api_dictor_main_demo, methods=["POST"])
    app.add_api_route("/api/dictors/{user_id}/demos", api_dictor_demo_upload, methods=["POST"])
    app.add_api_route("/api/deadlines/{deadline_id}/extend", api_deadline_extend, methods=["POST"])
    app.add_api_route("/api/deadlines/{deadline_id}/close", api_deadline_close, methods=["POST"])
