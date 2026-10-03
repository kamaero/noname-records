import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware

from app.auth import (
    get_user_roles as _get_user_roles,
    needs_password_setup as _needs_password_setup,
    verify_telegram_payload as _verify_telegram_payload,
)
from app.config import settings
from app.handler_factories import build_handler_registry
from app.rate_limit import limiter
from app.api.auditions_feed import api_auditions_feed
from app.api.dictors import register_dictors_routes
from app.api.telegram_webhook import api_telegram_webhook
from app.route_bootstrap import register_application_routes
from app.routers.auth import register_auth_routes
from app.services.audit import write_audit as _audit
from app.services.passwords import verify_password
from app.services.telegram_auth_bootstrap import bootstrap_telegram_auth_whitelist
from app.v2.illustrations_api import (
    api_v2_bind_illustration,
    api_v2_book_illustrations,
    api_v2_illustration_image,
)
from app.v2.lore_api import (
    api_v2_book_lore,
    api_v2_lore_article,
    api_v2_lore_image,
    api_v2_lore_search,
)
from app.v2.api import (
    api_v2_book_chapters,
    api_v2_cast,
    api_v2_character_map,
    api_v2_character_map_acknowledge,
    api_v2_character_map_adopt,
    api_v2_character_map_checked,
    api_v2_character_map_delete,
    api_v2_character_map_forget,
    api_v2_character_map_merge,
    api_v2_character_map_rename,
    api_v2_character_map_update,
    api_v2_chapter_script,
    api_v2_approve_all,
    api_v2_auto_publish,
    api_v2_chapter_approve,
    api_v2_create_role,
    api_v2_chapter_source,
    api_v2_consilium,
    api_v2_consilium_accept,
    api_v2_consilium_dismiss,
    api_v2_consilium_estimate,
    api_v2_consilium_recording_impact,
    api_v2_consilium_run,
    api_v2_consilium_stop,
    api_v2_disputed,
    api_v2_profile,
    api_v2_bind_author,
    api_v2_profile_apply,
    api_v2_profile_sync,
    api_v2_progress,
    api_v2_role_script,
    api_v2_role_traps,
    api_v2_run,
    api_v2_set_book_title,
    api_v2_set_model,
    api_v2_stop,
    api_v2_stress_queue,
    api_v2_character_about,
    api_v2_character_palette,
    api_v2_audition_audio,
    api_v2_take_audio,
    api_v2_book_auditions,
    api_v2_audition_reaction,
    api_v2_book_recording,
    api_v2_chapter_archive,
    api_v2_chapter_asr,
    api_v2_chapter_verify,
    api_v2_chapter_session_archive,
    api_v2_chapter_recording,
    api_v2_chapter_session,
    api_v2_homographs,
    api_v2_segment_stress,
    api_v2_stress_skip,
    api_v2_stress_term,
    api_v2_reassign_segment,
    api_v2_character_recast,
    api_v2_character_recast_preview,
    api_v2_cast_discrepancies,
    api_v2_publish,
    api_v2_unpublish,
)
from app.v2.ambient_api import (
    api_v2_ambient_audio,
    api_v2_ambient_chapter,
    api_v2_ambient_plan,
    api_v2_ambient_scene,
    api_v2_ambient_stop,
)
from app.v2.sound_api import (
    api_v2_sound_book,
    api_v2_sound_chapter,
    api_v2_sound_chapter_run,
    api_v2_sound_estimate,
    api_v2_sound_marker_add,
    api_v2_sound_marker_dismiss,
    api_v2_sound_marker_edit,
    api_v2_sound_pair_decide,
    api_v2_sound_place_edit,
    api_v2_sound_run,
    api_v2_sound_stop,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FRONTEND_DIST_DIR = os.path.join(_PROJECT_ROOT, "frontend", "dist")

app = FastAPI(title=settings.app_name)
app.add_middleware(GZipMiddleware, minimum_size=1200, compresslevel=5)


# Content-Security-Policy. Приложение — SPA (Vite-бандл со своего origin) плюс
# виджет Telegram Login, который грузит скрипт с telegram.org и встраивает iframe
# с oauth.telegram.org. style-src разрешает inline-стили, т.к. React активно
# использует атрибут style={...}. frame-ancestors 'none' заменяет X-Frame-Options.
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        # 'unsafe-eval' is required by the Telegram Login Widget: it invokes the
        # data-onauth callback via eval() (telegram-widget.js __parseFunction).
        # Scripts are still restricted to self + telegram.org; our Vite/React bundle
        # does not use eval. Without this, Telegram login silently fails after the
        # popup authorizes (the callback that submits the form is CSP-blocked).
        "script-src 'self' 'unsafe-eval' https://telegram.org",
        "frame-src https://oauth.telegram.org https://telegram.org",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    ]
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Единственный источник security-заголовков уровня приложения.

    nginx на периметре ставит только HSTS (заголовок уровня TLS). Остальные
    заголовки выставляются здесь, чтобы не было дубликатов и конфликтов значений
    (раньше X-Frame-Options был DENY в приложении и SAMEORIGIN в nginx).
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response


#: маршруты, по которым приходит аудио: для них действует отдельный, щедрый потолок
#: (settings.upload_max_bytes), а не общий лимит запроса.
#: Дубль Рассказчика — самый большой файл в студии: он говорит больше всех, и час его
#: речи в WAV занимает под гигабайт. Снимать предел со всего сразу нельзя — никто не
#: присылает полугигабайтный JSON, а безлимит на загрузку означал, что один кривой
#: запрос мог забить системный диск целиком, пока тело переливается на NAS.
UPLOAD_PATHS = (
    "/api/recording/batch",
    "/api/recording/role-take",
    "/api/recording/replica-patch",
)


def _format_bytes_for_dictor(num_bytes: int) -> str:
    """Байты в ГБ/МБ для сообщения диктору — не голое число, а то, что он поймёт."""
    gigabytes = num_bytes / (1024**3)
    if gigabytes >= 1:
        rounded = round(gigabytes)
        text = str(rounded) if abs(gigabytes - rounded) < 0.05 else f"{gigabytes:.1f}"
        return f"{text} ГБ"
    return f"{num_bytes / (1024**2):.0f} МБ"


def _refusal_message(request, limit: int) -> str:
    """Текст отказа по-русски: диктор должен понять, что случилось и что делать,
    а не увидеть голый код payload_too_large."""
    size_text = _format_bytes_for_dictor(limit)
    if request.url.path in UPLOAD_PATHS:
        return (
            f"Файл больше {size_text} — такой не принимаем. Обычный дубль на час в моно "
            "24 бит весит около 520 МБ; проверьте, не сохранился ли файл в несжатом "
            "стерео или не склеились ли несколько дублей в один."
        )
    return f"Тело запроса больше {size_text} — такой размер не поддерживается."


class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: FastAPI, *, max_body_bytes: int) -> None:
        super().__init__(app)
        self.max_body_bytes = max(1, int(max_body_bytes or 1))
        self.guarded_methods = {"POST", "PUT", "PATCH"}

    def limit_for(self, request) -> int:
        """Применимый предел для этого пути. Единственное место, где выбирается
        между потолком загрузки и общим лимитом — over_limit() и dispatch() звали
        его порознь, из-за чего отказ мог сообщить не тот предел, по которому
        на самом деле отказал."""
        return settings.upload_max_bytes if request.url.path in UPLOAD_PATHS else self.max_body_bytes

    def over_limit(self, request) -> bool:
        if request.method.upper() not in self.guarded_methods:
            return False
        raw_content_length = (request.headers.get("content-length") or "").strip()
        if not raw_content_length:
            return False
        try:
            return int(raw_content_length) > self.limit_for(request)
        except ValueError:
            return False

    async def dispatch(self, request: Request, call_next) -> Response:
        if self.over_limit(request):
            limit = self.limit_for(request)
            return JSONResponse(
                {
                    "ok": False,
                    "error": "payload_too_large",
                    "max_body_bytes": limit,
                    "content_length": int(request.headers.get("content-length") or 0),
                    "message": _refusal_message(request, limit),
                },
                status_code=413,
            )
        return await call_next(request)


app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if settings.api_rate_limit_enabled:
    app.add_middleware(SlowAPIMiddleware)

app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=settings.api_max_request_bytes)
app.add_middleware(SecurityHeadersMiddleware)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


def _bootstrap_runtime_config() -> None:
    bootstrap_telegram_auth_whitelist()



handler_registry = build_handler_registry(frontend_dist_dir=_FRONTEND_DIST_DIR)


def _spa_response(full_path: str = ""):
    return handler_registry.get("main_helpers")["spa_response"](full_path)


def _spa_index(_request: Request):
    return _spa_response()


def _spa_catchall(_request: Request, full_path: str):
    return _spa_response(full_path)


register_auth_routes(
    app,
    verify_password_cb=verify_password,
    audit_cb=_audit,
    get_user_roles_cb=_get_user_roles,
    needs_password_setup_cb=_needs_password_setup,
    verify_telegram_payload_cb=_verify_telegram_payload,
)

(
    dictor_upload_handlers,
    recording_handlers,
    books_list_handlers,
    book_actions_handlers,
    budget_api_handlers,
    frontend_core_handlers,
    system_routes_handlers,
) = handler_registry.get_many(
    "dictor_upload",
    "recording",
    "books_list",
    "book_actions",
    "budget_api",
    "frontend_core",
    "system_routes",
)

register_application_routes(
    app,
    dictor_upload_handlers=dictor_upload_handlers,
    recording_handlers=recording_handlers,
    books_list_handlers=books_list_handlers,
    book_actions_handlers=book_actions_handlers,
    budget_api_handlers=budget_api_handlers,
    frontend_core_handlers=frontend_core_handlers,
    system_routes_handlers=system_routes_handlers,
    spa_index=_spa_index,
    spa_catchall=_spa_catchall,
)

# Seeding the whitelist reads a table the schema work may have just changed, so it is
# registered AFTER the system routes: startup hooks run in registration order, and this
# one used to run first. Nothing noticed until revision 0014 added a column to
# `telegram_auth_accounts` and the app refused to start on a database at the previous
# revision. add_event_handler (not the deprecated @app.on_event decorator) — same hook,
# no DeprecationWarning; the system startup hook is registered the same way.
app.add_event_handler("startup", _bootstrap_runtime_config)

# Pipeline v2 reader routes: wired directly, bypassing the handler registry.
# Bot webhook: wired directly, like the v2 reader routes below.
app.add_api_route("/api/telegram/webhook", api_telegram_webhook, methods=["POST"])
# «Дикторы»: тоже напрямую, мимо реестра обработчиков.
register_dictors_routes(app)
app.add_api_route("/api/auditions/feed", api_auditions_feed, methods=["GET"])

app.add_api_route("/api/v2/chapters/{chapter_id}/script", api_v2_chapter_script, methods=["GET"])
app.add_api_route("/api/v2/chapters/{chapter_id}/source", api_v2_chapter_source, methods=["GET"])
app.add_api_route("/api/v2/chapters/{chapter_id}/approve", api_v2_chapter_approve, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/approve-all", api_v2_approve_all, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/auto-publish", api_v2_auto_publish, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/stress-skip", api_v2_stress_skip, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/homographs", api_v2_homographs, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/auditions", api_v2_book_auditions, methods=["GET"])
app.add_api_route("/api/v2/auditions/{audio_id}/audio", api_v2_audition_audio, methods=["GET"])
app.add_api_route("/api/v2/auditions/{audio_id}/reaction", api_v2_audition_reaction, methods=["POST"])
app.add_api_route("/api/v2/takes/{audio_id}/audio", api_v2_take_audio, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/recording", api_v2_book_recording, methods=["GET"])
app.add_api_route("/api/v2/chapters/{chapter_id}/recording", api_v2_chapter_recording, methods=["GET"])
app.add_api_route("/api/v2/chapters/{chapter_id}/archive.zip", api_v2_chapter_archive, methods=["GET"])
app.add_api_route("/api/v2/chapters/{chapter_id}/asr", api_v2_chapter_asr, methods=["POST"])
app.add_api_route("/api/v2/chapters/{chapter_id}/verify", api_v2_chapter_verify, methods=["POST"])
app.add_api_route("/api/v2/chapters/{chapter_id}/session-archive", api_v2_chapter_session_archive, methods=["POST"])
app.add_api_route("/api/v2/chapters/{chapter_id}/session.sesx", api_v2_chapter_session, methods=["GET"])
app.add_api_route("/api/v2/segments/{segment_id}/stress", api_v2_segment_stress, methods=["POST"])
app.add_api_route("/api/v2/characters/{character_id}/about", api_v2_character_about, methods=["POST"])
app.add_api_route("/api/v2/characters/{character_id}/palette", api_v2_character_palette, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/chapters", api_v2_book_chapters, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/role-script", api_v2_role_script, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/role-traps", api_v2_role_traps, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/disputed", api_v2_disputed, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/consilium", api_v2_consilium, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/consilium/estimate", api_v2_consilium_estimate, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/consilium/run", api_v2_consilium_run, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/consilium/stop", api_v2_consilium_stop, methods=["POST"])
app.add_api_route("/api/v2/consilium/{finding_id}/accept", api_v2_consilium_accept, methods=["POST"])
app.add_api_route("/api/v2/consilium/{finding_id}/dismiss", api_v2_consilium_dismiss, methods=["POST"])
app.add_api_route("/api/v2/consilium/{finding_id}/recording-impact", api_v2_consilium_recording_impact, methods=["GET"])
app.add_api_route("/api/v2/chapters/{chapter_id}/sound", api_v2_sound_chapter, methods=["GET"])
app.add_api_route("/api/v2/chapters/{chapter_id}/sound/markers", api_v2_sound_marker_add, methods=["POST"])
app.add_api_route("/api/v2/sound/markers/{marker_id}", api_v2_sound_marker_edit, methods=["PATCH"])
app.add_api_route("/api/v2/sound/markers/{marker_id}", api_v2_sound_marker_dismiss, methods=["DELETE"])
app.add_api_route("/api/v2/books/{book_id}/sound", api_v2_sound_book, methods=["GET"])
app.add_api_route("/api/v2/sound/places/{place_id}", api_v2_sound_place_edit, methods=["PATCH"])
app.add_api_route("/api/v2/sound/pairs/{pair_id}", api_v2_sound_pair_decide, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/sound/estimate", api_v2_sound_estimate, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/sound/run", api_v2_sound_run, methods=["POST"])
app.add_api_route("/api/v2/chapters/{chapter_id}/sound/run", api_v2_sound_chapter_run, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/sound/stop", api_v2_sound_stop, methods=["POST"])
app.add_api_route("/api/v2/chapters/{chapter_id}/ambient", api_v2_ambient_chapter, methods=["POST"])
app.add_api_route("/api/v2/chapters/{chapter_id}/ambient/plan", api_v2_ambient_plan, methods=["GET"])
app.add_api_route("/api/v2/chapters/{chapter_id}/ambient/stop", api_v2_ambient_stop, methods=["POST"])
app.add_api_route("/api/v2/sound/markers/{marker_id}/ambient", api_v2_ambient_scene, methods=["POST"])
app.add_api_route("/api/v2/ambient/{audio_file_id}/audio", api_v2_ambient_audio, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/stress-queue", api_v2_stress_queue, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/stress-term", api_v2_stress_term, methods=["POST"])
app.add_api_route("/api/v2/segments/{segment_id}/attribution", api_v2_reassign_segment, methods=["POST"])
app.add_api_route("/api/v2/characters/{character_id}/recast", api_v2_character_recast, methods=["POST"])
app.add_api_route("/api/v2/characters/{character_id}/recast-preview", api_v2_character_recast_preview, methods=["GET"])
app.add_api_route("/api/v2/authors/{author_id}/cast-discrepancies", api_v2_cast_discrepancies, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/publish", api_v2_publish, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/unpublish", api_v2_unpublish, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/cast", api_v2_cast, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/character-map", api_v2_character_map, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/character-map/delete", api_v2_character_map_delete, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/character-map/adopt", api_v2_character_map_adopt, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/character-map/merge", api_v2_character_map_merge, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/character-map/rename", api_v2_character_map_rename, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/character-map/update", api_v2_character_map_update, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/character-map/acknowledge", api_v2_character_map_acknowledge, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/character-map/forget", api_v2_character_map_forget, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/character-map/checked", api_v2_character_map_checked, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/characters", api_v2_create_role, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/profile", api_v2_profile, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/profile/sync", api_v2_profile_sync, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/profile/apply", api_v2_profile_apply, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/author", api_v2_bind_author, methods=["POST"])
# Pipeline v2 run control: progress is readable by anyone with a session, the rest is for editors.
app.add_api_route("/api/v2/books/{book_id}/progress", api_v2_progress, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/model", api_v2_set_model, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/title", api_v2_set_book_title, methods=["POST"])
# Лор: шпаргалка по миру книги. Единственный слой, сделанный прежде всего для диктора,
# поэтому доступ — любому вошедшему, без роли редактора.
app.add_api_route("/api/v2/books/{book_id}/lore", api_v2_book_lore, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/lore/search", api_v2_lore_search, methods=["GET"])
app.add_api_route("/api/v2/lore/articles/{article_id}", api_v2_lore_article, methods=["GET"])
app.add_api_route("/api/v2/lore/images/{author_id}/{image_key}", api_v2_lore_image, methods=["GET"])
# Иллюстрации книги: привязку делает редактор, смотрит портрет любой вошедший.
app.add_api_route("/api/v2/books/{book_id}/illustrations", api_v2_book_illustrations, methods=["GET"])
app.add_api_route("/api/v2/illustrations/{illustration_id}", api_v2_bind_illustration, methods=["POST"])
app.add_api_route("/api/v2/illustrations/{illustration_id}/image", api_v2_illustration_image, methods=["GET"])
app.add_api_route("/api/v2/books/{book_id}/run", api_v2_run, methods=["POST"])
app.add_api_route("/api/v2/books/{book_id}/stop", api_v2_stop, methods=["POST"])
