from fastapi import FastAPI

from app.route_bootstrap import register_application_routes


def _noop(*_args, **_kwargs):
    return None


def _register(app: FastAPI, startup_called: list[str]) -> None:
    register_application_routes(
        app,
        dictor_upload_handlers={"dictor_pro_batch_validate": _noop},
        recording_handlers={
            "recording_replica_patch": _noop,
            "recording_batch": _noop,
            "recording_workspace": _noop,
            "recording_filename_hint": _noop,
            "recording_delete_audio": _noop,
        },
        books_list_handlers={"api_books": _noop},
        book_actions_handlers={
            "api_upload_book": _noop,
            "api_book_repair_action": _noop,
            "api_delete_book": _noop,
        },
        budget_api_handlers={
            "api_book_budget": _noop,
            "api_save_book_budget": _noop,
            "api_save_book_card": _noop,
            "api_save_budget_character": _noop,
            "api_studio_rate": _noop,
            "api_save_studio_rate": _noop,
        },
        frontend_core_handlers={
            "api_public_auth_config": _noop,
            "api_me": _noop,
            "api_me_bot_reach": _noop,
            "api_onboarding_shown": _noop,
            "api_users": _noop,
            "api_attach_telegram": _noop,
            "api_bot_reach": _noop,
            "api_users_export": _noop,
            "api_users_issue_passwords": _noop,
            "api_create_user": _noop,
            "api_update_user": _noop,
            "api_reset_user_password": _noop,
            "api_delete_user": _noop,
            "api_bulk_user_roles": _noop,
            "api_merge_users": _noop,
            "api_save_telegram_whitelist": _noop,
            "api_log": _noop,
        },
        system_routes_handlers={
            "startup": lambda: startup_called.append("called"),
            "health": _noop,
            "index_redirect": _noop,
            "dashboard_redirect": _noop,
            "workspace_redirect": _noop,
            "admin_redirect": _noop,
            "validation_redirect": _noop,
        },
        spa_index=_noop,
        spa_catchall=_noop,
    )


def test_register_application_routes_keeps_system_startup_handler() -> None:
    app = FastAPI()
    startup_called: list[str] = []
    _register(app, startup_called)

    assert len(app.router.on_startup) == 1
    app.router.on_startup[0]()
    assert startup_called == ["called"]


def test_register_application_routes_exposes_the_surviving_spa_paths() -> None:
    app = FastAPI()
    _register(app, [])
    paths = {route.path for route in app.routes}

    for path in (
        "/api/me",
        "/api/me/bot-reach",
        "/api/me/onboarding-shown",
        "/api/books",
        "/api/books/upload",
        "/api/books/{book_id}/repair",
        "/api/books/{book_id}/delete",
        "/api/budget/{book_id}",
        "/api/budget/character/{char_id}",
        "/api/settings/rate",
        "/api/recording/workspace",
        "/api/recording/filename",
        "/api/recording/files/{audio_id}/delete",
        "/dictor-pro/batch-validate",
        "/api/users",
        "/api/users/{user_id}/telegram",
        "/api/users/bot-reach",
        "/api/users/export.txt",
        "/api/users/issue-passwords",
        "/api/log",
        "/health",
        "/app",
    ):
        assert path in paths, path

    # The v1 pipeline surfaces are gone with it.
    for gone in (
        "/api/workspace/init", "/api/validation/workspace", "/api/chapters/{chapter_id}/view", "/api/asr/jobs", "/api/daw/jobs",
        "/dictor-pro/send-mobile-link", "/dictor-pro/progress", "/dictor-pro/export-role.txt", "/api/recording/role-take",
        "/api/books/{book_id}/readiness", "/api/books/{book_id}/char-map", "/api/books/{book_id}/char-memory",
    ):
        assert gone not in paths, gone
