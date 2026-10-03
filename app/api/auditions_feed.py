"""GET /api/auditions/feed — раздел «Пробы». Права как у листа проб в касте: слушает
каждый вошедший; реакции чужих проб видят владелец и автор; утверждают админ и автор;
👍/👎 ставят автор и владелец студии. `?since=` — только число новых (для счётчика в меню)."""
from fastapi import Request
from fastapi.responses import JSONResponse

from app.api._helpers import bad_request_response, unauthorized_response
from app.auth import can_react_to_auditions, has_any_role, is_authenticated, session_payload
from app.db import SessionLocal
from app.services.auditions_feed import feed, new_count, parse_since


def api_auditions_feed(request: Request):
    if not is_authenticated(request):
        return unauthorized_response()
    can_approve = has_any_role(request, {"author"})
    viewer = str(session_payload(request).get("display_name") or "").strip()
    with SessionLocal() as db:
        items = feed(db, viewer_name=viewer, sees_all=can_approve)
    since_raw = request.query_params.get("since")
    if since_raw is not None:
        since = parse_since(since_raw)
        if since is None:
            return bad_request_response("bad_since")
        return JSONResponse({"ok": True, "new_count": new_count(items, since)})
    return JSONResponse({"ok": True, "items": items, "can_approve": can_approve,
                         "can_react": can_react_to_auditions(request)})
