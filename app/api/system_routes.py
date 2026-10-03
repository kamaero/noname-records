from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import Request, status
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)


def build_system_routes_handlers(deps: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    app_name = deps["app_name"]
    is_authenticated = deps["is_authenticated"]
    workspace_tab_url = deps["workspace_tab_url"]
    init_db_cb = deps["init_db_cb"]
    ensure_admin_account_cb = deps["ensure_admin_account_cb"]
    start_nas_probe_worker_cb = deps["start_nas_probe_worker_cb"]
    start_mirror_worker_cb = deps["start_mirror_worker_cb"]

    def startup() -> None:
        init_db_cb()  # alembic upgrade head — additive migrations now run via revision 0004
        ensure_admin_account_cb()
        # Сторож NAS и зеркалирование — фоновые потоки: должны встать при старте
        # веб-процесса, а не по первому запросу к ним.
        #
        # Результат стартера больше не выбрасывается молча: ровно так фича однажды и
        # оказалась инертной — сторож не поднялся после рестарта (строка `running`
        # мёртвого процесса выглядела живой), а startup спокойно пошёл дальше, и узнать
        # об этом было неоткуда. Теперь несостоявшийся запуск слышно в журнале сервиса.
        for label, started in (
            ("Сторож NAS", start_nas_probe_worker_cb()),
            ("Зеркало NAS", start_mirror_worker_cb()),
        ):
            if not started:
                logger.warning("%s не запущен при старте процесса — загрузки останутся без него", label)

    def health() -> dict[str, str]:
        return {"status": "ok", "app": app_name}

    def _to_app(request: Request, target: str):
        if is_authenticated(request):
            return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)
        return RedirectResponse(url="/app/login", status_code=status.HTTP_302_FOUND)

    def index_redirect(request: Request):
        return _to_app(request, "/app/books")

    def dashboard_redirect(request: Request):
        return _to_app(request, "/app/books")

    def workspace_redirect(request: Request):
        if not is_authenticated(request):
            return RedirectResponse(url="/app/login", status_code=status.HTTP_302_FOUND)
        tab = str(request.query_params.get("tab") or "").strip().lower()
        route_map = {
            "book_prep": "/app/prep",
            "validation": "/app/check",
            "recording": "/app/recording",
            "asr_daw": "/app/asr-daw",
        }
        if tab in route_map:
            return RedirectResponse(url=route_map[tab], status_code=status.HTTP_302_FOUND)
        return RedirectResponse(url=workspace_tab_url("book_prep"), status_code=status.HTTP_302_FOUND)

    # The server-rendered admin/validation portals are gone; bookmarks land on the SPA.
    def admin_redirect(request: Request):
        return _to_app(request, "/app/users")

    def validation_redirect(request: Request):
        return _to_app(request, "/app/check")

    return {
        "startup": startup,
        "health": health,
        "index_redirect": index_redirect,
        "dashboard_redirect": dashboard_redirect,
        "workspace_redirect": workspace_redirect,
        "admin_redirect": admin_redirect,
        "validation_redirect": validation_redirect,
    }
