import json
from typing import Any

from fastapi import Request

from app.auth import session_payload
from app.models import AuditLog


AUDIT_ACTION_LABELS = {
    "login_password": "Password sign-in",
    "login_telegram": "Telegram sign-in",
    "upload_and_enqueue": "Book processing started",
    "stop_pipeline": "Book processing stopped",
    "pause_pipeline": "Book processing paused",
    "continue_pipeline": "Book processing resumed",
    "delete_book": "Book deleted",
    "create_whitelist": "Access granted",
    "update_whitelist": "Access changed",
    "password_issued_by_bot": "Password issued by bot",
}


def audit_action_label(action: str) -> str:
    raw = str(action or "").strip()
    return AUDIT_ACTION_LABELS.get(raw, raw)


def write_audit(
    db,
    request: Request | None,
    *,
    entity_type: str,
    entity_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    user_id: str = "",
) -> None:
    actor_id = user_id or (str(session_payload(request).get("uid") or "") if request else "")
    db.add(
        AuditLog(
            user_id=actor_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
        )
    )
