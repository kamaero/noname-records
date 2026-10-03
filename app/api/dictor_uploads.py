from __future__ import annotations

from typing import Any, Callable

from fastapi import Request
from fastapi.responses import JSONResponse

from app.api._helpers import bad_request_response, forbidden_response, unauthorized_response
from app.constants import DICTOR_ROLES
from app.services.upload_kind import classify_upload_kind


def build_dictor_upload_handlers(deps: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    """Pre-upload filename validation for Dictor Pro batches, plus the recording access
    guard shared with `app.api.recording` (the uploads themselves go through /api/recording/*)."""
    is_authenticated = deps["is_authenticated"]
    has_any_role = deps["has_any_role"]
    has_workspace_full_access = deps["has_workspace_full_access"]
    session_local = deps["SessionLocal"]
    parse_batch_audio_filename = deps["parse_batch_audio_filename"]
    apply_batch_overrides = deps["apply_batch_overrides"]

    def unauthorized():
        return unauthorized_response()

    def forbidden():
        return forbidden_response()

    def require_recording_access(request: Request):
        # Unified gate for the single Recording section: full workspace access OR any recording tier.
        if not is_authenticated(request):
            return unauthorized()
        if has_workspace_full_access(request) or has_any_role(request, {"admin", *DICTOR_ROLES}):
            return None
        return forbidden()

    async def dictor_pro_batch_validate(request: Request):
        # Тот же вход, что и у самой загрузки: предпросмотр — её часть, а не отдельное право.
        # Раньше он спрашивал роль `dictor_pro` — её не носила ни одна учётка, и предпросмотр
        # молча отвечал 403 всем дикторам сразу. Роль с тех пор упразднена совсем.
        access_error = require_recording_access(request)
        if access_error:
            return access_error

        payload = await request.json()
        files = payload.get("files") if isinstance(payload, dict) else []
        overrides = payload.get("overrides") if isinstance(payload, dict) else []
        default_book_code = str((payload or {}).get("book_code") or "").strip()
        default_actor_name = str((payload or {}).get("actor_name") or "").strip()
        if not isinstance(files, list):
            return bad_request_response()
        if not isinstance(overrides, list):
            overrides = []

        parsed_items: list[dict] = []
        with session_local() as kinds_db:
            for idx, item in enumerate(files):
                if not isinstance(item, dict):
                    continue
                override = dict(overrides[idx]) if idx < len(overrides) and isinstance(overrides[idx], dict) else {}
                # Вид загрузки — не то, что выбрал диктор, а то, утверждён ли он на роль.
                override["kind"] = classify_upload_kind(
                    kinds_db,
                    role=str(override.get("role") or "").strip(),
                    actor_name=str(override.get("actor_name") or default_actor_name or "").strip(),
                )
                if idx < len(overrides):
                    overrides[idx] = override
                else:
                    overrides.append(override)

        for idx, item in enumerate(files):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            size = int(item.get("size") or 0)
            parsed = parse_batch_audio_filename(name, default_book_code=default_book_code, default_actor_name=default_actor_name)
            override = overrides[idx] if idx < len(overrides) else None
            parsed = apply_batch_overrides(parsed, override)
            parsed["index"] = idx
            parsed["size_bytes"] = max(0, size)
            parsed_items.append(parsed)

        # Предпросмотр видит только имя, не содержимое — «уже загружен» по имени тут
        # больше не проверяем: дозапись всей роли и короткий фикс приходят под своим
        # обычным именем, а совпадение имени само по себе ничего не значит (решение
        # владельца 2026-09-15). Содержимое сверяет сама загрузка (`recording_batch`).
        for item in parsed_items:
            item["ok"] = len(item["errors"]) == 0

        valid_count = sum(1 for x in parsed_items if x.get("ok"))
        return JSONResponse(
            {
                "ok": True,
                "items": parsed_items,
                "summary": {
                    "total": len(parsed_items),
                    "valid": valid_count,
                    "invalid": len(parsed_items) - valid_count,
                },
            }
        )

    return {
        "dictor_pro_batch_validate": dictor_pro_batch_validate,
        "require_recording_access": require_recording_access,
    }
