"""Месячный лимит на ручках запуска платных прогонов.

«Сверх лимита» приходит параметром `?override_limit=1` на любой ручке — у части ручек нет
тела запроса, и один способ проще для страницы. Разрешено только администратору."""
from fastapi import Request
from fastapi.responses import JSONResponse

from app.api._helpers import error_response
from app.auth import is_owner_telegram, session_payload, session_roles
from app.services import spend


def limit_gate(db, request: Request, *, estimate_rub: float, unknown_price: bool, what: str) -> JSONResponse | None:
    """None — можно запускать; иначе готовый ответ (409 с цифрами или 403)."""
    override = str(request.query_params.get("override_limit") or "").lower() in ("1", "true", "yes")
    admin = "admin" in session_roles(request) or is_owner_telegram(request)
    try:
        decision = spend.check_start(db, estimate_rub=estimate_rub, unknown_price=unknown_price, override=override,
                                     is_admin=admin, actor_uid=str(session_payload(request).get("uid") or ""), what=what)
    except PermissionError:
        return error_response("override_admin_only", status_code=403)
    if not decision.allowed:
        return JSONResponse(decision.payload(), status_code=409)
    if decision.unknown_price:
        request.state.spend_warning = ("У модели этого шага нет цены: траты прогона не войдут в лимит. "
                                       "Впишите цену в «Настройки → Нейросети».")
    return None


def with_warning(request: Request, body: dict) -> dict:
    """Успешный ответ запуска с предупреждением гейта, если оно было."""
    warning = getattr(request.state, "spend_warning", "")
    return {**body, "spend_warning": warning} if warning else body
