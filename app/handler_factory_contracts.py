"""Dependency contracts for every handler group the registry builds.

One tuple per `build_*_handlers` factory: the keys that module reads from `deps`
(`deps["..."]`) minus what the factory itself injects. tests/test_handler_dependency_contracts.py
checks each tuple against the module source and against `build_handler_deps()`.
"""
from __future__ import annotations

from collections.abc import Iterable

BOOKS_LIST_HANDLER_DEP_KEYS = (
    "is_authenticated",
    "SessionLocal",
    "func",
    "ScriptBook",
    "Character",
    "collect_books_progress",
    "serialize_book_summary",
)

BOOK_ACTIONS_HANDLER_DEP_KEYS = (
    "is_authenticated",
    "has_workspace_full_access",
    "session_payload",
    "session_display_name",
    "SessionLocal",
    "get_book",
    "enqueue_book",
    "audit",
    "ScriptLog",
    "OperatorIntervention",
)

FRONTEND_CORE_HANDLER_DEP_KEYS = (
    "is_authenticated",
    "is_owner_telegram",
    "session_payload",
    "session_roles",
    "session_auth_source",
    "session_telegram_user_id",
    "has_workspace_full_access",
    "workspace_tabs_for_request",
    "SessionLocal",
    "build_owner_dashboard_data",
    "User",
    "TelegramAuthAccount",
    "get_user_roles",
    "format_dt",
    "audit",
    "settings",
    "WORKSPACE_TABS",
    "needs_password_setup",
)

BUDGET_API_HANDLER_DEP_KEYS = (
    "is_authenticated",
    "has_workspace_full_access",
    "has_any_role",
    "is_agent",
    "session_payload",
    "session_roles",
    "session_display_name",
    "SessionLocal",
    "get_book",
    "build_character_style_maps",
    "format_dt",
    "BookBudget",
    "Character",
    "CharacterBudgetSnapshot",
    "ScriptChapter",
)

MAIN_HELPERS_DEP_KEYS = (
    "User",
    "UserRole",
    "settings",
    "SessionLocal",
    "frontend_dist_file",
    "frontend_dist_dir",
    "needs_password_setup",
)

DICTOR_UPLOAD_HANDLER_DEP_KEYS = (
    "is_authenticated",
    "has_any_role",
    "has_workspace_full_access",
    "SessionLocal",
    "parse_batch_audio_filename_service",
    "normalize_role_label",
    "apply_batch_overrides_service",
)

RECORDING_HANDLER_DEP_KEYS = (
    "is_authenticated",
    "has_any_role",
    "has_workspace_full_access",
    "session_payload",
    "is_agent",
    "SessionLocal",
    "AudioFile",
    "build_canonical_audio_filename",
    "send_telegram_message",
    "recording_identity",
    "recording_workspace_payload",
    "store_audio_file_service",
    "safe_name",
    "parse_batch_audio_filename_service",
    "normalize_role_label",
)

SYSTEM_ROUTES_HANDLER_DEP_KEYS = (
    "app_name",
    "is_authenticated",
    "workspace_tab_url",
    "init_db_cb",
)

STARTUP_RUNTIME_HELPERS_DEP_KEYS = (
    "settings",
    "SessionLocal",
    "BackgroundRun",
    "nas_probe_loop_worker",
    "start_nas_probe_background",
    "mirror_loop_worker",
    "start_mirror_background",
)

# Every handler group and the contract it is built from. `build_handler_deps()` must
# satisfy the union; the registry test builds each group once.
HANDLER_CONTRACTS: dict[str, tuple[str, ...]] = {
    "books_list": BOOKS_LIST_HANDLER_DEP_KEYS,
    "book_actions": BOOK_ACTIONS_HANDLER_DEP_KEYS,
    "frontend_core": FRONTEND_CORE_HANDLER_DEP_KEYS,
    "budget_api": BUDGET_API_HANDLER_DEP_KEYS,
    "main_helpers": MAIN_HELPERS_DEP_KEYS,
    "dictor_upload": DICTOR_UPLOAD_HANDLER_DEP_KEYS,
    "recording": RECORDING_HANDLER_DEP_KEYS,
    "system_routes": SYSTEM_ROUTES_HANDLER_DEP_KEYS,
    "startup_runtime_helpers": STARTUP_RUNTIME_HELPERS_DEP_KEYS,
}


def select_deps(source: dict, keys: tuple[str, ...]) -> dict:
    missing = tuple(key for key in keys if key not in source)
    if missing:
        available = ", ".join(sorted(source))
        missing_list = ", ".join(missing)
        raise KeyError(f"Missing dependency keys: {missing_list}. Available keys: {available}")
    return {key: source[key] for key in keys}


def contract_missing_keys(source: dict, keys: Iterable[str]) -> tuple[str, ...]:
    return tuple(key for key in keys if key not in source)
