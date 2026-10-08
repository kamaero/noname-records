"""Вход в настольную версию по ключу запуска и сторож адреса.

Логина нет — программа одного человека. Но сайт на 127.0.0.1 достаётся любой открытой в
браузере странице: запрос на localhost и подмена DNS (rebinding). Поэтому: ключ, который
знает только оболочка, cookie только этого запуска (SameSite=Strict) и проверка Host.
"""
import hmac

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth import session_serializer
from app.config import settings
from app.db import SessionLocal
from app.seat import ALLOWED_HOSTS, is_hidden, local_owner, one_seat, seat_fingerprint


async def api_seat_enter(request: Request):
    if not one_seat():
        return JSONResponse({"error": "not_found"}, status_code=404)
    given = str(request.query_params.get("token") or "")
    expected = str(settings.seat_token or "")
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        return JSONResponse({"error": "Откройте Noname Records заново."}, status_code=401)
    with SessionLocal() as db:
        user = local_owner(db)
        payload = {"uid": user.id, "sub": user.login, "roles": ["admin", "author"],
                   "display_name": user.display_name, "auth_source": "seat", "seat": seat_fingerprint()}
    response = RedirectResponse(url="/app/", status_code=302)
    response.set_cookie("session", session_serializer.dumps(payload), httponly=True, samesite="strict",
                        secure=False, path="/")
    return response


class SeatGuardMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if one_seat():
            host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
            if host not in ALLOWED_HOSTS:
                return JSONResponse({"error": "bad_host"}, status_code=400)
            if is_hidden(request.url.path):
                return JSONResponse({"error": "not_found"}, status_code=404)
        return await call_next(request)


def register_seat_routes(app) -> None:
    app.add_api_route("/seat", api_seat_enter, methods=["GET"])
    app.add_middleware(SeatGuardMiddleware)
