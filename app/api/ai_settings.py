"""/api/settings/ai — «Настройки → Нейросети»: ключи провайдеров, проверка, балансы, модели шагов.

Только администратор: ключи — это деньги студии. Ключ не уходит в браузер ни в одном
ответе — только последние четыре символа и откуда он взят. Каждое изменение пишется в
журнал без значения ключа. Сетевые проверки идут в потоке: единственный uvicorn не должен
замирать на 15 секунд, пока провайдер думает. Подключено напрямую в `app/main.py`.
"""
import json

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api._helpers import forbidden_response, unauthorized_response
from app.auth import is_authenticated, is_owner_telegram, session_payload, session_roles
from app.db import SessionLocal
from app.models import AuditLog
from app.services import provider_keys, step_models
from app.services.provider_checks import balance, check_key
from app.services.step_models import PROVIDER_LABELS


def _gate(request: Request):
    if not is_authenticated(request):
        return unauthorized_response()
    if not ("admin" in session_roles(request) or is_owner_telegram(request)):
        return forbidden_response()
    return None


def _actor(request: Request) -> tuple[str, str]:
    payload = session_payload(request)
    return str(payload.get("uid") or ""), str(payload.get("display_name") or "")


def _log(db, request: Request, entity_type: str, entity_id: str, action: str, payload: dict) -> None:
    uid, _name = _actor(request)
    db.add(AuditLog(user_id=uid, entity_type=entity_type, entity_id=entity_id, action=action,
                    payload_json=json.dumps(payload, ensure_ascii=False)))


def _provider_view(db, name: str) -> dict:
    return {**provider_keys.key_info(db, name), "label": PROVIDER_LABELS.get(name, name)}


def _bad(text: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": text}, status_code=status)


async def api_ai_settings(request: Request):
    if (error := _gate(request)) is not None:
        return error
    with SessionLocal() as db:
        # у кого ключ есть (без значения) — в том числе у тех, кто задаётся только в .env (Azure)
        keyed = sorted(name for name in provider_keys.ENV_NAMES if provider_keys.provider_key(name))
        return {"providers": [_provider_view(db, name) for name in provider_keys.PROVIDERS],
                "steps": step_models.steps_view(db), "keyed": keyed}


def _check_and_record(name: str) -> dict:
    result = check_key(name, provider_keys.provider_key(name))
    with SessionLocal() as db:
        provider_keys.record_check(db, name, result.status, result.detail)
        db.commit()
    return {"status": result.status, "detail": result.detail}


async def api_ai_key_save(request: Request, provider: str):
    if (error := _gate(request)) is not None:
        return error
    if provider not in provider_keys.PROVIDERS:
        return _bad("Этот провайдер задаётся только в .env.", 404)
    body = await request.json()
    _uid, name = _actor(request)
    try:
        with SessionLocal() as db:
            provider_keys.set_key(db, provider, str((body or {}).get("key") or ""), actor=name)
            db.commit()
    except ValueError:
        return _bad("Вставьте ключ — поле пустое.")
    checked = await run_in_threadpool(_check_and_record, provider)
    with SessionLocal() as db:
        _log(db, request, "provider_key", provider, "key_saved", {"check": checked["status"]})
        db.commit()
        return _provider_view(db, provider)


async def api_ai_key_delete(request: Request, provider: str):
    if (error := _gate(request)) is not None:
        return error
    if provider not in provider_keys.PROVIDERS:
        return _bad("Этот провайдер задаётся только в .env.", 404)
    _uid, name = _actor(request)
    with SessionLocal() as db:
        provider_keys.delete_key(db, provider, actor=name)
        _log(db, request, "provider_key", provider, "key_deleted", {})
        db.commit()
        return _provider_view(db, provider)


async def api_ai_key_check(request: Request, provider: str):
    if (error := _gate(request)) is not None:
        return error
    if provider not in provider_keys.PROVIDERS:
        return _bad("Этот провайдер задаётся только в .env.", 404)
    if not provider_keys.provider_key(provider):
        return _bad("Ключа нет — проверять нечего.")
    checked = await run_in_threadpool(_check_and_record, provider)
    with SessionLocal() as db:
        _log(db, request, "provider_key", provider, "key_checked", {"check": checked["status"]})
        db.commit()
        return _provider_view(db, provider)


async def api_ai_balances(request: Request):
    if (error := _gate(request)) is not None:
        return error

    def read_all() -> list[dict]:
        out = []
        for name in provider_keys.PROVIDERS:
            key = provider_keys.provider_key(name)
            out.append(balance(name, key) if key else {"provider": name, "available": False, "amount": None,
                                                       "unit": "", "detail": "нет ключа"})
        return out

    return {"balances": await run_in_threadpool(read_all)}


async def api_ai_step(request: Request, step: str):
    if (error := _gate(request)) is not None:
        return error
    if step not in step_models.STEPS:
        return _bad("Нет такого шага.", 404)
    body = await request.json() or {}
    _uid, name = _actor(request)
    with SessionLocal() as db:
        if body.get("reset"):
            step_models.reset_step_model(db, step)
            _log(db, request, "step_model", step, "step_reset", {})
        else:
            try:
                step_models.set_step_model(db, step, str(body.get("provider") or ""), str(body.get("model") or ""),
                                           actor=name)
            except ValueError:
                return _bad("Этот шаг не работает с таким провайдером или модель не указана.")
            _log(db, request, "step_model", step, "step_set",
                 {"provider": body.get("provider"), "model": body.get("model")})
        db.commit()
        return next(item for item in step_models.steps_view(db) if item["step"] == step)


async def api_spend(request: Request):
    if (error := _gate(request)) is not None:
        return error
    from app.services import spend

    def read():
        with SessionLocal() as db:
            listed = spend.months(db)
            month = str(request.query_params.get("month") or listed[0])
            if month not in listed:
                return None
            return {**spend.month_summary(db, month), "months": listed}

    body = await run_in_threadpool(read)
    return body if body is not None else _bad("Нет такого месяца.", 404)


async def api_spend_limit(request: Request):
    if (error := _gate(request)) is not None:
        return error
    from app.services.studio_settings import studio_settings

    value = (await request.json() or {}).get("limit_rub")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or value != int(value):
        return _bad("Лимит — целое число рублей, 0 — без лимита.")
    _uid, name = _actor(request)
    with SessionLocal() as db:
        row = studio_settings(db)
        before = int(row.monthly_limit_rub or 0)
        row.monthly_limit_rub, row.updated_by = int(value), name
        _log(db, request, "spend_limit", "monthly", "limit_set", {"from": before, "to": int(value)})
        db.commit()
    return {"ok": True, "limit_rub": int(value)}


def _price_fields(body: dict):
    """Проверенные поля цены или текст ошибки."""
    provider, model = str(body.get("provider") or "").strip(), str(body.get("model") or "").strip()
    unit, currency = str(body.get("unit") or ""), str(body.get("currency") or "")
    if not provider or not model:
        return "Укажите провайдера и модель."
    if currency not in ("RUB", "USD"):
        return "Валюта — RUB или USD."

    def number(name):
        value = body.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(name)
        return float(value)
    try:
        if unit == "tokens":
            return {"provider": provider, "model": model, "unit": unit, "currency": currency,
                    "price_in": number("price_in"), "price_out": number("price_out"), "price_unit": None}
        if unit == "seconds":
            return {"provider": provider, "model": model, "unit": unit, "currency": currency,
                    "price_in": None, "price_out": None, "price_unit": number("price_unit")}
    except ValueError:
        return "Цена — неотрицательное число."
    return "Единица — tokens или seconds."


async def api_price_save(request: Request):
    if (error := _gate(request)) is not None:
        return error
    from app.models import ModelPrice

    fields = _price_fields(await request.json() or {})
    if isinstance(fields, str):
        return _bad(fields)
    _uid, name = _actor(request)
    with SessionLocal() as db:
        row = db.get(ModelPrice, (fields["provider"], fields["model"])) or ModelPrice(
            provider=fields["provider"], model=fields["model"])
        for key in ("unit", "price_in", "price_out", "price_unit", "currency"):
            setattr(row, key, fields[key])
        row.updated_by = name
        db.add(row)
        _log(db, request, "model_price", f"{fields['provider']}:{fields['model']}"[:36], "price_set", fields)
        db.commit()
    return {"ok": True}


async def api_price_delete(request: Request):
    if (error := _gate(request)) is not None:
        return error
    from app.models import ModelPrice

    provider = str(request.query_params.get("provider") or "")
    model = str(request.query_params.get("model") or "")
    with SessionLocal() as db:
        row = db.get(ModelPrice, (provider, model))
        if row is not None:
            db.delete(row)
            _log(db, request, "model_price", f"{provider}:{model}"[:36], "price_reset", {})
            db.commit()
    return {"ok": True}


def register_ai_settings_routes(app) -> None:
    app.add_api_route("/api/settings/spend", api_spend, methods=["GET"])
    app.add_api_route("/api/settings/spend/limit", api_spend_limit, methods=["PUT"])
    app.add_api_route("/api/settings/ai/prices", api_price_save, methods=["PUT"])
    app.add_api_route("/api/settings/ai/prices", api_price_delete, methods=["DELETE"])
    app.add_api_route("/api/settings/ai", api_ai_settings, methods=["GET"])
    app.add_api_route("/api/settings/ai/balances", api_ai_balances, methods=["POST"])
    app.add_api_route("/api/settings/ai/keys/{provider}", api_ai_key_save, methods=["PUT"])
    app.add_api_route("/api/settings/ai/keys/{provider}", api_ai_key_delete, methods=["DELETE"])
    app.add_api_route("/api/settings/ai/keys/{provider}/check", api_ai_key_check, methods=["POST"])
    app.add_api_route("/api/settings/ai/steps/{step}", api_ai_step, methods=["PUT"])
