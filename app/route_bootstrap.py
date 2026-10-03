"""Wire the handler groups from the registry onto the FastAPI app.

Everything here is a v1-era HTTP surface that the SPA still uses (books list,
upload/repair/delete, budget, recording, users/log, system redirects). The pipeline v2 routes are
added directly in `app.main`.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from app.routers.audio import register_audio_routes
from app.routers.frontend import register_frontend_routes
from app.routers.system import register_system_routes


def register_application_routes(
    app: FastAPI,
    *,
    dictor_upload_handlers: dict[str, Any],
    recording_handlers: dict[str, Any],
    books_list_handlers: dict[str, Any],
    book_actions_handlers: dict[str, Any],
    budget_api_handlers: dict[str, Any],
    frontend_core_handlers: dict[str, Any],
    system_routes_handlers: dict[str, Any],
    spa_index,
    spa_catchall,
) -> None:
    register_audio_routes(
        app,
        handlers={
            "batch_validate_uploads": dictor_upload_handlers["dictor_pro_batch_validate"],
            "recording_replica_patch": recording_handlers["recording_replica_patch"],
            "recording_batch": recording_handlers["recording_batch"],
            "recording_workspace": recording_handlers["recording_workspace"],
            "recording_filename_hint": recording_handlers["recording_filename_hint"],
            "recording_delete_audio": recording_handlers["recording_delete_audio"],
        },
    )

    register_system_routes(
        app,
        handlers={
            "startup": system_routes_handlers["startup"],
            "health": system_routes_handlers["health"],
            "index_redirect": system_routes_handlers["index_redirect"],
            "dashboard_redirect": system_routes_handlers["dashboard_redirect"],
            "workspace_redirect": system_routes_handlers["workspace_redirect"],
            "admin_redirect": system_routes_handlers["admin_redirect"],
            "validation_redirect": system_routes_handlers["validation_redirect"],
        },
    )

    register_frontend_routes(
        app,
        handlers={
            "api_public_auth_config": frontend_core_handlers["api_public_auth_config"],
            "api_me": frontend_core_handlers["api_me"],
            "api_me_bot_reach": frontend_core_handlers["api_me_bot_reach"],
            "api_onboarding_shown": frontend_core_handlers["api_onboarding_shown"],
            "api_books": books_list_handlers["api_books"],
            "api_upload_book": book_actions_handlers["api_upload_book"],
            "api_book_repair_action": book_actions_handlers["api_book_repair_action"],
            "api_delete_book": book_actions_handlers["api_delete_book"],
            "api_book_budget": budget_api_handlers["api_book_budget"],
            "api_save_book_budget": budget_api_handlers["api_save_book_budget"],
            "api_save_book_card": budget_api_handlers["api_save_book_card"],
            "api_save_budget_character": budget_api_handlers["api_save_budget_character"],
            "api_studio_rate": budget_api_handlers["api_studio_rate"],
            "api_save_studio_rate": budget_api_handlers["api_save_studio_rate"],
            "api_users": frontend_core_handlers["api_users"],
            "api_attach_telegram": frontend_core_handlers["api_attach_telegram"],
            "api_bot_reach": frontend_core_handlers["api_bot_reach"],
            "api_users_export": frontend_core_handlers["api_users_export"],
            "api_users_issue_passwords": frontend_core_handlers["api_users_issue_passwords"],
            "api_create_user": frontend_core_handlers["api_create_user"],
            "api_update_user": frontend_core_handlers["api_update_user"],
            "api_reset_user_password": frontend_core_handlers["api_reset_user_password"],
            "api_delete_user": frontend_core_handlers["api_delete_user"],
            "api_bulk_user_roles": frontend_core_handlers["api_bulk_user_roles"],
            "api_merge_users": frontend_core_handlers["api_merge_users"],
            "api_save_telegram_whitelist": frontend_core_handlers["api_save_telegram_whitelist"],
            "api_log": frontend_core_handlers["api_log"],
            "spa_index": spa_index,
            "spa_catchall": spa_catchall,
        },
    )
