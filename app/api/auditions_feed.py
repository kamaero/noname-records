"""GET /api/auditions/feed — раздел «Пробы». Права как у листа проб в касте: слушает
каждый вошедший; оценки видят и ставят автор (верхняя шкала) и владелец студии (нижняя);
утверждают и отказывают админ и автор. `?since=` — только число новых проб (для счётчика в меню)."""
from fastapi import Body, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api._helpers import bad_request_response, forbidden_response, not_found_response, unauthorized_response
from app.auth import audition_score_lanes, has_any_role, is_authenticated, session_payload
from app.db import SessionLocal
from app.services.audition_rejections import RejectError, cancel_pending_rejection, reject_by_hand, rejection_view
from app.services.auditions_feed import feed, new_count, parse_since


def api_auditions_feed(request: Request):
    if not is_authenticated(request):
        return unauthorized_response()
    can_approve = has_any_role(request, {"author"})
    lanes = audition_score_lanes(request)
    viewer = str(session_payload(request).get("display_name") or "").strip()
    with SessionLocal() as db:
        items = feed(db, viewer_name=viewer, sees_scores=bool(lanes))
    since_raw = request.query_params.get("since")
    if since_raw is not None:
        since = parse_since(since_raw)
        if since is None:
            return bad_request_response("bad_since")
        return JSONResponse({"ok": True, "new_count": new_count(items, since)})
    return JSONResponse({"ok": True, "items": items, "can_approve": can_approve, "can_score": lanes})


def api_auditions_reject(request: Request, body: dict = Body(default_factory=dict)):
    """«Отказать» / «Не отказывать» (`cancel: true`) по паре актёр + роль. Права — как у
    «Утвердить»: решение по роли принимают админ и автор, а не каждый, кто слушает."""
    if not is_authenticated(request):
        return unauthorized_response()
    if not has_any_role(request, {"author"}):
        return forbidden_response()
    body = body if isinstance(body, dict) else {}
    pair = {"book_id": str(body.get("book_id") or "").strip(), "role": str(body.get("role") or ""),
            "actor_name": str(body.get("actor_name") or "")}
    action = cancel_pending_rejection if body.get("cancel") else reject_by_hand
    with SessionLocal() as db:
        try:
            try:
                rej = action(db, **pair)
                db.commit()
            except IntegrityError:
                # Двое нажали одновременно (или оценка завела отказ в ту же секунду) —
                # строка пары уже есть, второй проход её найдёт.
                db.rollback()
                rej = action(db, **pair)
                db.commit()
        except RejectError as error:
            db.rollback()
            if error.code == "not_found":
                return not_found_response("audition_not_found")
            return bad_request_response(error.code)
        return JSONResponse({"ok": True, "rejection": rejection_view(rej)})
