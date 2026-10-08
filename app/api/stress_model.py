"""/api/settings/stress-model — модель ударений в «Настройках» настольной версии.

Только администратор (в «одном месте» это владелец программы). На VPS модель качается
сама — ручка отвечает «auto», а кнопка не нужна.
"""
from fastapi import Request
from fastapi.responses import JSONResponse

from app.api._helpers import forbidden_response, unauthorized_response
from app.auth import is_authenticated, is_owner_telegram, session_roles
from app.seat import one_seat
from app.services import stress_model


def _gate(request: Request):
    if not is_authenticated(request):
        return unauthorized_response()
    if not ("admin" in session_roles(request) or is_owner_telegram(request)):
        return forbidden_response()
    return None


async def api_stress_model(request: Request):
    return _gate(request) or stress_model.state()


async def api_stress_model_download(request: Request):
    denied = _gate(request)
    if denied:
        return denied
    if not one_seat():
        return JSONResponse({"error": "На сервере модель скачивается сама."}, status_code=409)
    stress_model.start_download()
    return stress_model.state()


def register_stress_model_routes(app) -> None:
    app.add_api_route("/api/settings/stress-model", api_stress_model, methods=["GET"])
    app.add_api_route("/api/settings/stress-model", api_stress_model_download, methods=["POST"])
