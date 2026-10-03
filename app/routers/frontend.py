from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def register_frontend_routes(app: FastAPI, *, handlers: dict[str, Callable]) -> None:
    app.add_api_route("/api/public/auth-config", handlers["api_public_auth_config"], methods=["GET"])
    app.add_api_route("/api/me", handlers["api_me"], methods=["GET"])
    app.add_api_route("/api/me/bot-reach", handlers["api_me_bot_reach"], methods=["GET"])
    app.add_api_route("/api/me/onboarding-shown", handlers["api_onboarding_shown"], methods=["POST"])
    app.add_api_route("/api/books", handlers["api_books"], methods=["GET"])
    app.add_api_route("/api/books/upload", handlers["api_upload_book"], methods=["POST"])
    app.add_api_route("/api/books/{book_id}/repair", handlers["api_book_repair_action"], methods=["POST"])
    app.add_api_route("/api/books/{book_id}/delete", handlers["api_delete_book"], methods=["POST"])
    app.add_api_route("/api/budget/{book_id}", handlers["api_book_budget"], methods=["GET"])
    app.add_api_route("/api/budget/{book_id}", handlers["api_save_book_budget"], methods=["POST"])
    app.add_api_route("/api/budget/{book_id}/card", handlers["api_save_book_card"], methods=["POST"])
    app.add_api_route("/api/budget/character/{char_id}", handlers["api_save_budget_character"], methods=["POST"])
    app.add_api_route("/api/settings/rate", handlers["api_studio_rate"], methods=["GET"])
    app.add_api_route("/api/settings/rate", handlers["api_save_studio_rate"], methods=["POST"])
    app.add_api_route("/api/users", handlers["api_users"], methods=["GET"])
    app.add_api_route("/api/users", handlers["api_create_user"], methods=["POST"])
    app.add_api_route("/api/users/bulk-roles", handlers["api_bulk_user_roles"], methods=["POST"])
    app.add_api_route("/api/users/merge", handlers["api_merge_users"], methods=["POST"])
    app.add_api_route("/api/users/{user_id}", handlers["api_update_user"], methods=["PATCH"])
    app.add_api_route("/api/users/{user_id}", handlers["api_delete_user"], methods=["DELETE"])
    app.add_api_route("/api/users/{user_id}/password", handlers["api_reset_user_password"], methods=["POST"])
    app.add_api_route("/api/users/telegram-whitelist", handlers["api_save_telegram_whitelist"], methods=["POST"])
    app.add_api_route("/api/users/{user_id}/telegram", handlers["api_attach_telegram"], methods=["POST"])
    app.add_api_route("/api/users/bot-reach", handlers["api_bot_reach"], methods=["GET"])
    app.add_api_route("/api/users/export.txt", handlers["api_users_export"], methods=["GET"])
    app.add_api_route("/api/users/issue-passwords", handlers["api_users_issue_passwords"], methods=["POST"])
    app.add_api_route("/api/log", handlers["api_log"], methods=["GET"])
    app.add_api_route("/app", handlers["spa_index"], methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/app/{full_path:path}", handlers["spa_catchall"], methods=["GET"], response_class=HTMLResponse)
