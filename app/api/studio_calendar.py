"""Профиль показа дат доступен после входа и не допускает смены финансового календаря."""
from fastapi import Request
from fastapi.responses import JSONResponse

from app.auth import is_authenticated
from app.db import SessionLocal
from app.services.studio_calendar import calendar_profile


def api_studio_calendar(request: Request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    with SessionLocal() as db:
        return {"ok": True, **calendar_profile(db)}
