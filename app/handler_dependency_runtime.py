from __future__ import annotations

import os

from sqlalchemy import func

from app.auth import (
    get_user_roles as _get_user_roles,
    has_any_role as _has_any_role,
    has_workspace_full_access as _has_workspace_full_access,
    is_agent as _is_agent,
    is_authenticated as _is_authenticated,
    is_owner_telegram as _is_owner_telegram,
    needs_password_setup as _needs_password_setup,
    session_auth_source as _session_auth_source,
    session_display_name as _session_display_name,
    session_payload as _session_payload,
    session_roles as _session_roles,
    session_telegram_user_id as _session_telegram_user_id,
)
from app.config import settings
from app.constants import status_label as _status_label
from app.db import SessionLocal, run_migrations
from app.models import ScriptBook
from app.services.audit import write_audit as _audit
from app.services.shared_runtime import (
    format_dt as _format_dt,
    get_book as _get_book,
    normalize_role_label as _normalize_role_label,
    safe_name as _safe_name,
)
from app.workspace_access import (
    workspace_tab_url as _workspace_tab_url,
    workspace_tabs_for_request as _workspace_tabs_for_request,
)


def _frontend_dist_file(frontend_dist_dir: str, *parts: str) -> str:
    return os.path.join(frontend_dist_dir, *parts)


def _serialize_book_summary(book: ScriptBook, progress: dict | None) -> dict[str, object]:
    return {
        "id": book.id,
        "title": book.title,
        "author": str(getattr(book, "author_label", "") or ""),
        "display_title": book.display_title or book.title,
        "source_filename": book.source_filename,
        "source_format": book.source_format,
        "status": str(book.status or ""),
        "status_label": _status_label(str(book.status or "")),
        "stop_requested": bool(book.stop_requested and str(book.stop_requested).lower() not in ("false", "0", "")),
        "chapter_count": int(book.chapter_count or 0),
        "book_annotation": str(book.book_annotation or ""),
        "pipeline_mode": str(getattr(book, "pipeline_mode", "") or "standard"),
        "validation_profile": str(getattr(book, "validation_profile", "") or "operator_only"),
        "total_chars": int(book.total_chars or 0),
        "created_at": _format_dt(book.created_at),
        "updated_at": _format_dt(book.updated_at),
        "progress": progress or None,
        "urls": {
            "workspace": _workspace_tab_url("book_prep", book_id=book.id),
            "validation": _workspace_tab_url("validation", book_id=book.id),
            "recording": f"/app/recording?book_id={book.id}",
        },
    }


def build_base_runtime_deps(*, frontend_dist_dir: str) -> dict:
    return {
        "app_name": settings.app_name,
        "settings": settings,
        "SessionLocal": SessionLocal,
        "func": func,
        "init_db_cb": run_migrations,
        "is_authenticated": _is_authenticated,
        "has_any_role": _has_any_role,
        "has_workspace_full_access": _has_workspace_full_access,
        "is_agent": _is_agent,
        "is_owner_telegram": _is_owner_telegram,
        "needs_password_setup": _needs_password_setup,
        "session_auth_source": _session_auth_source,
        "session_display_name": _session_display_name,
        "session_payload": _session_payload,
        "session_roles": _session_roles,
        "session_telegram_user_id": _session_telegram_user_id,
        "get_user_roles": _get_user_roles,
        "workspace_tabs_for_request": _workspace_tabs_for_request,
        "workspace_tab_url": _workspace_tab_url,
        "audit": _audit,
        "format_dt": _format_dt,
        "get_book": _get_book,
        "safe_name": _safe_name,
        "normalize_role_label": _normalize_role_label,
        "serialize_book_summary": _serialize_book_summary,
        "frontend_dist_file": _frontend_dist_file,
        "frontend_dist_dir": frontend_dist_dir,
    }
