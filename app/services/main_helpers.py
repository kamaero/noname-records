from __future__ import annotations

import os
from typing import Any, Callable

from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse


def build_main_helpers(deps: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    """SPA file serving and the admin account bootstrap the web process runs at start."""
    user_model = deps["User"]
    user_role_model = deps["UserRole"]
    settings = deps["settings"]
    session_local = deps["SessionLocal"]
    frontend_dist_file = deps["frontend_dist_file"]
    frontend_dist_dir = deps["frontend_dist_dir"]
    needs_password_setup = deps["needs_password_setup"]

    def spa_build_missing_frontend_response() -> HTMLResponse:
        return HTMLResponse(
            (
                "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>NONAME Frontend</title></head><body style='font-family:sans-serif;padding:32px'>"
                "<h1>Frontend scaffold is ready</h1>"
                "<p>Собери Vite-приложение командой <code>npm install && npm run build</code> в каталоге <code>frontend/</code>.</p>"
                "<p><a href='/app/login'>Открыть SPA</a></p>"
                "</body></html>"
            ),
            status_code=200,
        )

    #: Каталог, куда Vite кладёт собранное, давая каждому файлу имя с хешем содержимого.
    #: Правило — по каталогу, а не по виду имени: настоящие хеши выглядят как `DRrXxguD`
    #: и `B6ZC8CTn`, и никакая эвристика не отличит их от осмысленного слова. Сторож,
    #: следящий, чтобы сюда не попал файл без хеша, стоит в `tests/test_spa_caching.py`.
    ASSETS_DIR = "assets"

    #: Год. Содержимое такого файла не меняется никогда: меняется — меняется и имя,
    #: значит и адрес. Перепроверять нечего.
    IMMUTABLE = "public, max-age=31536000, immutable"

    #: Не «не кешируй», а «спрашивай каждый раз, прежде чем взять из кеша».
    #: Условный запрос здесь всё равно не сработает — голый `FileResponse` не смотрит
    #: на `If-None-Match`, это умеет только `StaticFiles`, — поэтому ответ приходит
    #: целиком, а не как 304. Для `index.html` в килобайт это ничего не стоит, а
    #: гарантия свежести важнее.
    REVALIDATE = "no-cache"

    def spa_response(full_path: str = ""):
        """Файл из сборки или точка входа — и правило кеширования к нему.

        Точка входа и файлы с хешем живут по противоположным правилам, и до сих пор
        жили по одному — никакому. `index.html` уходил без `Cache-Control`, браузер
        кешировал его по своему усмотрению, и после выката человек продолжал работать
        со вчерашней сборкой, пока не нажимал Ctrl+Shift+R.

        Отсутствующий файл в `assets/` — это 404, а не точка входа. Заглушка SPA ловила
        и его: браузер просил скрипт, получал HTML с кодом 200, отказывался выполнять
        его из-за `nosniff` и показывал белый экран вместо ошибки. По 404 он
        перезагружает страницу начисто и приходит за свежим `index.html`.

        Отсутствующий *маршрут* — другое дело: `/app/books/42/cast` файлом никогда и
        не был, его по-прежнему обслуживает точка входа.
        """
        requested = (full_path or "").strip("/")
        segments = requested.split("/") if requested else []
        if segments:
            candidate = os.path.abspath(frontend_dist_file(*segments))
            dist_root = os.path.abspath(frontend_dist_dir)
            inside_dist = candidate.startswith(dist_root + os.sep)
            if inside_dist and os.path.isfile(candidate):
                cache = IMMUTABLE if segments[0] == ASSETS_DIR else REVALIDATE
                return FileResponse(candidate, headers={"Cache-Control": cache})
            if segments[0] == ASSETS_DIR:
                return PlainTextResponse("Not Found", status_code=404)
        index_path = frontend_dist_file("index.html")
        if os.path.isfile(index_path):
            return FileResponse(index_path, headers={"Cache-Control": REVALIDATE})
        return spa_build_missing_frontend_response()

    def ensure_admin_account() -> None:
        if needs_password_setup():
            return
        with session_local() as db:
            user = db.query(user_model).filter(user_model.login == settings.admin_login).first()
            if not user:
                user = user_model(
                    login=settings.admin_login,
                    password_hash=settings.admin_password_hash,
                    display_name="Administrator",
                    is_active="true",
                )
                db.add(user)
                db.flush()
            has_admin_role = db.query(user_role_model).filter(user_role_model.user_id == user.id, user_role_model.role == "admin").first()
            if not has_admin_role:
                db.add(user_role_model(user_id=user.id, role="admin"))
            db.commit()

    return {
        "spa_response": spa_response,
        "ensure_admin_account": ensure_admin_account,
    }
