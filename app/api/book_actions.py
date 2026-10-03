from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Callable

from fastapi import Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from app.api._helpers import forbidden_response, not_found_response, unauthorized_response
from app.config import settings
from app.db import transactional
from app.services.book_purge import book_file_dirs, purge_book_rows
from app.services.pipeline_observability import enforce_hotfix_guardrails, ensure_pipeline_run, log_pipeline_event

VALIDATION_PROFILES = {"operator_only", "author_assisted"}
# Operator actions on a v2 run: the run itself is driven by /api/v2/books/{id}/run
# and /stop; these only flip the book's `stop_requested` flag that v2 checks between
# chapters (stop) or clear it so the next run is allowed (continue).
REPAIR_ACTIONS = ("stop_pipeline", "pause_pipeline", "continue_pipeline")


def _normalize_validation_profile(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    return normalized if normalized in VALIDATION_PROFILES else "operator_only"


def build_book_actions_handlers(deps: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    """Upload, operator repair (stop/continue flags) and delete for a book.

    Every upload is a pipeline v2 book: `enqueue_book` stores the chapters and parks
    the book as `uploaded`; nothing runs until the operator asks v2 to.
    """
    is_authenticated = deps["is_authenticated"]
    has_workspace_full_access = deps["has_workspace_full_access"]
    session_payload = deps["session_payload"]
    session_display_name = deps["session_display_name"]
    session_local = deps["SessionLocal"]
    get_book = deps["get_book"]
    enqueue_book = deps["enqueue_book"]
    audit = deps["audit"]
    script_log_model = deps["ScriptLog"]
    operator_intervention_model = deps["OperatorIntervention"]

    def require_full_workspace_access(request: Request):
        if not is_authenticated(request):
            return unauthorized_response()
        if not has_workspace_full_access(request):
            return forbidden_response()
        return None

    def _apply_repair_action(db, *, request: Request, book, action_type: str, reason: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        actor_user_id = str(session_payload(request).get("uid") or "").strip()
        actor_name = session_display_name(request) or "unknown"
        run = ensure_pipeline_run(
            db,
            book=book,
            triggered_by="operator",
            start_reason_code="RUN_OPERATOR_REPAIR",
            start_reason=f"Оператор: {action_type}",
        )
        before = {"book_status": str(book.status or ""), "stop_requested": str(book.stop_requested or "")}

        if action_type in ("stop_pipeline", "pause_pipeline"):
            book.stop_requested = "true"
            if book.status in ("queued", "processing"):
                book.status = "stopping"
        elif action_type == "continue_pipeline":
            book.stop_requested = "false"
        else:
            raise ValueError(f"unsupported_action:{action_type}")

        after = {"book_status": str(book.status or ""), "stop_requested": str(book.stop_requested or "")}
        db.add(
            operator_intervention_model(
                book_id=book.id,
                pipeline_run_id=run.id,
                actor_user_id=actor_user_id,
                actor_name=actor_name,
                action_type=action_type,
                reason=(reason or "").strip()[:500],
                payload_json=json.dumps({"before": before, "after": after, "payload": payload}, ensure_ascii=False),
            )
        )
        log_pipeline_event(
            db,
            book_id=book.id,
            pipeline_run_id=run.id,
            event_type="operator_repair",
            level="warning",
            code=f"OPERATOR_{action_type.upper()}",
            message=f"Операторское вмешательство: {action_type}",
            details={"actor_user_id": actor_user_id, "actor_name": actor_name, "reason": reason[:300]},
        )
        guardrails = enforce_hotfix_guardrails(
            db,
            book=book,
            max_manual_actions_24h=int(settings.pipeline_hotfix_max_manual_actions_24h or 6),
        )
        db.add(
            script_log_model(
                book_id=book.id,
                level="warning",
                pipeline_run_id=run.id,
                event_code=f"OPERATOR_{action_type.upper()}",
                event_details_json=json.dumps({"reason": reason, "guardrails": guardrails}, ensure_ascii=False),
                message=(
                    f"Операторское вмешательство: {action_type}. "
                    f"manual_actions_24h={int(guardrails['manual_actions_24h'])} "
                    f"needs_investigation={int(bool(guardrails['needs_investigation']))}"
                ),
            )
        )
        return {"run_id": run.id, "guardrails": guardrails, "before": before, "after": after}

    async def api_upload_book(
        request: Request,
        validation_profile: str = "",
        genre_guidelines: str = "",
        domain_lexicon: str = "",
        title: str = "",
        author: str = "",
        file: UploadFile | None = None,
    ):
        access_error = require_full_workspace_access(request)
        if access_error:
            return access_error
        if file is None:
            return JSONResponse({"ok": False, "error": "file_required"}, status_code=status.HTTP_400_BAD_REQUEST)

        payload = await file.read()
        with session_local() as db:
            try:
                with transactional(db):
                    current_user_id = str(session_payload(request).get("uid") or "").strip()
                    book = enqueue_book(
                        db=db,
                        filename=file.filename or "book.txt",
                        payload=payload,
                        pipeline_mode="v2",
                        validation_profile=_normalize_validation_profile(validation_profile),
                        genre_guidelines=(genre_guidelines or settings.default_genre_guidelines).strip(),
                        domain_lexicon=(domain_lexicon or settings.default_domain_lexicon).strip(),
                        created_by_user_id=current_user_id,
                        created_by_name=session_display_name(request),
                        title=title,
                        author=author,
                    )
                    audit(
                        db,
                        request,
                        entity_type="script_book",
                        entity_id=book.id,
                        action="upload_and_enqueue",
                        payload={
                            "title": book.title,
                            "author": book.author_label,
                            "pipeline_mode": book.pipeline_mode,
                            "validation_profile": book.validation_profile,
                        },
                    )
                return JSONResponse(
                    {
                        "ok": True,
                        "book_id": book.id,
                        "book_title": book.display_title or book.title,
                        "source_filename": book.source_filename,
                        "status": book.status,
                        "chapter_count": int(book.chapter_count or 0),
                        "total_chars": int(book.total_chars or 0),
                        "pipeline_mode": book.pipeline_mode,
                        "validation_profile": book.validation_profile,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"ok": False, "error": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST)

    async def api_book_repair_action(request: Request, book_id: str):
        access_error = require_full_workspace_access(request)
        if access_error:
            return access_error
        try:
            payload = await request.json()
        except ValueError:  # пустое или не-JSON тело — действия нет, ответ ниже скажет какое
            payload = {}
        action_type = str((payload or {}).get("action_type") or "").strip().lower()
        reason = str((payload or {}).get("reason") or "").strip()
        if action_type not in REPAIR_ACTIONS:
            return JSONResponse(
                {"ok": False, "error": "unsupported_action_type", "supported": list(REPAIR_ACTIONS)},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if not reason:
            return JSONResponse({"ok": False, "error": "reason_required"}, status_code=status.HTTP_400_BAD_REQUEST)
        with session_local() as db:
            book = get_book(db, book_id)
            if not book:
                return not_found_response()
            try:
                with transactional(db):
                    result = _apply_repair_action(db, request=request, book=book, action_type=action_type, reason=reason, payload=payload)
                    audit(
                        db,
                        request,
                        entity_type="script_book",
                        entity_id=book.id,
                        action="repair_action",
                        payload={"title": book.title, "action_type": action_type, "reason": reason, **result},
                    )
                return JSONResponse({"ok": True, "repair_action": result})
            except Exception as exc:  # noqa: BLE001
                return JSONResponse({"ok": False, "error": f"repair_failed:{type(exc).__name__}"}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def api_delete_book(request: Request, book_id: str):
        access_error = require_full_workspace_access(request)
        if access_error:
            return access_error
        with session_local() as db:
            book = get_book(db, book_id)
            if not book:
                return not_found_response()

            def _delete_book_graph() -> None:
                book.stop_requested = "true"
                # Что уходит вместе с книгой, а что её переживает, решает один список
                # в `book_purge` — ручной перечень здесь отстал от схемы на v2, консилиум,
                # звук и эмбиент.
                purge_book_rows(db, book_id)
                audit(db, request, entity_type="script_book", entity_id=book.id, action="delete_book", payload={"title": book.title})
                db.delete(book)

            def _remove_book_files() -> None:
                for folder in book_file_dirs(book_id):
                    shutil.rmtree(Path(folder), ignore_errors=True)

            try:
                _delete_book_graph()
                db.commit()
                _remove_book_files()
                return JSONResponse({"ok": True})
            except DatabaseError as exc:
                db.rollback()
                if "malformed" not in str(exc).lower() and "disk image" not in str(exc).lower():
                    return JSONResponse({"ok": False, "error": f"delete_failed:{type(exc).__name__}"}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
                # SQLite index corruption hotfix: rebuild indexes and retry delete once.
                try:
                    db.execute(text("REINDEX"))
                    db.commit()
                    book = get_book(db, book_id)
                    if not book:
                        return JSONResponse({"ok": True})
                    _delete_book_graph()
                    db.commit()
                    _remove_book_files()
                    return JSONResponse({"ok": True})
                except Exception as retry_exc:  # noqa: BLE001
                    db.rollback()
                    return JSONResponse({"ok": False, "error": f"delete_failed:{type(retry_exc).__name__}"}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
            except Exception as exc:
                db.rollback()
                return JSONResponse({"ok": False, "error": f"delete_failed:{type(exc).__name__}"}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return {
        "api_upload_book": api_upload_book,
        "api_book_repair_action": api_book_repair_action,
        "api_delete_book": api_delete_book,
    }
