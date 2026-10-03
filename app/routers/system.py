from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def register_system_routes(app: FastAPI, *, handlers: dict[str, Callable]) -> None:
    if "startup" in handlers:
        app.add_event_handler("startup", handlers["startup"])
    app.add_api_route("/health", handlers["health"], methods=["GET"])
    app.add_api_route("/", handlers["index_redirect"], methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/dashboard", handlers["dashboard_redirect"], methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/workspace", handlers["workspace_redirect"], methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/admin", handlers["admin_redirect"], methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/validation", handlers["validation_redirect"], methods=["GET"])
