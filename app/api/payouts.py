"""Ручки «Сметы» — только владельцу (спека 2026-10-06-payouts).

Синхронные: FastAPI зовёт их в потоке, и сборка «Сметы» (~0,7 с на проде) не замораживает
единственный uvicorn. Запись — под `WRITE_LOCK`, вместе с коммитом: две быстрые «Выплачено»
иначе обе прочли бы старый остаток (ревью)."""
from __future__ import annotations

from datetime import date

from fastapi import Body, Request
from fastapi.responses import JSONResponse, Response

from app.auth import is_authenticated, session_display_name
from app.db import SessionLocal
from app.services.payouts import (
    WRITE_LOCK, PayoutError, cancel, collect, export_xlsx, is_owner_request, pay_role, person_advance,
)
from app.time_utils import utcnow_naive

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _refused(db, request):
    if not is_authenticated(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    if not is_owner_request(db, request):
        return JSONResponse({"ok": False, "error": "forbidden"}, status_code=403)
    return None


def _dict(body) -> dict:
    return body if isinstance(body, dict) else {}


def api_payouts(request: Request):
    with SessionLocal() as db:
        if (refused := _refused(db, request)) is not None:
            return refused
        return JSONResponse({"ok": True, **collect(db, now=utcnow_naive())})


def api_payouts_pay(request: Request, body: dict = Body(default_factory=dict)):
    body = _dict(body)
    with WRITE_LOCK, SessionLocal() as db:
        if (refused := _refused(db, request)) is not None:
            return refused
        try:
            rows = pay_role(db, book_id=str(body.get("book_id") or ""), role_key=str(body.get("role_key") or ""),
                            actor_name=str(body.get("actor_name") or ""), kind=str(body.get("kind") or ""),
                            amount_rub=body.get("amount_rub"), by=session_display_name(request) or "",
                            now=utcnow_naive())
        except PayoutError as error:
            return JSONResponse({"ok": False, "error": error.code}, status_code=400)
        db.commit()
        return JSONResponse({"ok": True, "written": [{"kind": r.kind, "amount_rub": r.amount_rub} for r in rows]})


def api_payouts_person_advance(request: Request, body: dict = Body(default_factory=dict)):
    body = _dict(body)
    with WRITE_LOCK, SessionLocal() as db:
        if (refused := _refused(db, request)) is not None:
            return refused
        try:
            row = person_advance(db, person_key=str(body.get("person_key") or ""), amount_rub=body.get("amount_rub"),
                                 note=str(body.get("note") or ""), by=session_display_name(request) or "",
                                 now=utcnow_naive())
        except PayoutError as error:
            status_code = 404 if error.code == "person_not_found" else 400
            return JSONResponse({"ok": False, "error": error.code}, status_code=status_code)
        db.commit()
        return JSONResponse({"ok": True, "written": [{"kind": row.kind, "amount_rub": row.amount_rub}]})


def api_payouts_cancel(request: Request, body: dict = Body(default_factory=dict)):
    body = _dict(body)
    with WRITE_LOCK, SessionLocal() as db:
        if (refused := _refused(db, request)) is not None:
            return refused
        try:
            cancel(db, str(body.get("payment_id") or ""), by=session_display_name(request) or "", now=utcnow_naive())
        except PayoutError as error:
            return JSONResponse({"ok": False, "error": error.code}, status_code=404)
        db.commit()
        return JSONResponse({"ok": True})


def api_payouts_export(request: Request):
    with SessionLocal() as db:
        if (refused := _refused(db, request)) is not None:
            return refused
        today = date.today()
        content = export_xlsx(collect(db, now=utcnow_naive()), today=today)
    return Response(content=content, media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="smeta-{today:%Y-%m-%d}.xlsx"'})


def register_payouts_routes(app) -> None:
    app.add_api_route("/api/payouts", api_payouts, methods=["GET"])
    app.add_api_route("/api/payouts/pay", api_payouts_pay, methods=["POST"])
    app.add_api_route("/api/payouts/person-advance", api_payouts_person_advance, methods=["POST"])
    app.add_api_route("/api/payouts/cancel", api_payouts_cancel, methods=["POST"])
    app.add_api_route("/api/payouts/export.xlsx", api_payouts_export, methods=["GET"])
