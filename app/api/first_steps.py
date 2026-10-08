"""/api/first-steps — карточка «Первые шаги» на главной.

Только администратор: шаги — про ключи, модели и деньги студии. Импорт примера идёт
в потоке: разбор текста и запись глав не должны держать единственный uvicorn.
Подключено напрямую в `app/main.py`, как «Настройки → Нейросети».
"""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api._helpers import forbidden_response, unauthorized_response
from app.auth import is_authenticated, is_owner_telegram, session_payload, session_roles
from app.db import SessionLocal
from app.services import first_steps


def _gate(request: Request):
    if not is_authenticated(request):
        return unauthorized_response()
    if not ("admin" in session_roles(request) or is_owner_telegram(request)):
        return forbidden_response()
    return None


def _checklist() -> dict:
    with SessionLocal() as db:
        return first_steps.checklist(db)


async def api_first_steps(request: Request):
    if (error := _gate(request)) is not None:
        return error
    # смета примера ходит по главам книги — тоже не в цикле событий
    return await run_in_threadpool(_checklist)


async def api_first_steps_sample(request: Request):
    if (error := _gate(request)) is not None:
        return error
    payload = session_payload(request)

    def work() -> dict:
        with SessionLocal() as db:
            book = first_steps.import_sample(db, user_id=str(payload.get("uid") or ""),
                                            user_name=str(payload.get("display_name") or ""))
            return {"book_id": book.id}

    return await run_in_threadpool(work)


async def api_first_steps_seen(request: Request):
    if (error := _gate(request)) is not None:
        return error
    body = await request.json()
    book_id = str((body or {}).get("book_id") or "") if isinstance(body, dict) else ""
    with SessionLocal() as db:
        return {"ok": first_steps.mark_seen(db, book_id)}


async def api_first_steps_flags(request: Request):
    if (error := _gate(request)) is not None:
        return error
    body = await request.json()
    flags = {k: body.get(k) for k in ("hidden", "no_limit") if isinstance(body, dict) and k in body}
    if not flags or any(not isinstance(v, bool) for v in flags.values()):
        return JSONResponse({"error": "Ожидаются hidden и/или no_limit: true или false."}, status_code=400)
    with SessionLocal() as db:
        first_steps.set_flags(db, **flags)
    return await run_in_threadpool(_checklist)


def register_first_steps_routes(app) -> None:
    app.add_api_route("/api/first-steps", api_first_steps, methods=["GET"])
    app.add_api_route("/api/first-steps", api_first_steps_flags, methods=["PUT"])
    app.add_api_route("/api/first-steps/sample", api_first_steps_sample, methods=["POST"])
    app.add_api_route("/api/first-steps/seen", api_first_steps_seen, methods=["POST"])
