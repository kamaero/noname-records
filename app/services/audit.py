import json
from typing import Any

from fastapi import Request

from app.auth import session_payload
from app.models import AuditLog


AUDIT_ACTION_LABELS = {
    "login_password": "Вход по паролю",
    "login_telegram": "Вход через Telegram",
    "upload_and_enqueue": "Запуск авторазметки",
    "stop_pipeline": "Остановка пайплайна",
    "pause_pipeline": "Пауза пайплайна",
    "continue_pipeline": "Продолжение пайплайна",
    "delete_book": "Удаление книги",
    "create_whitelist": "Добавление в whitelist",
    "update_whitelist": "Изменение whitelist",
    "password_issued_by_bot": "Пароль выдан ботом",
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
