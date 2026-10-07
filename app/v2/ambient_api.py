"""Ручки эмбиента: генерация по главе, перегенерация одной сцены, прослушивание трека.

Только для редактора, как и звуковая разметка (`app.v2.sound_api`): дикторам эмбиент не
показан. Генерация — фоновый прогон `ambient_engine` в очереди `consilium`.

Два запуска одной главы разом не допускаются своей проверкой, а не только дедупликацией
очереди: та забывает прогон после ~30 с молчания пульса, а один трек ElevenLabs сочиняет
минутами — без проверки вторая кнопка поставила бы второй прогон, а перегенерация сцены
молча пропала бы. Идущий прогон главы — всегда 409 `already_running`; очередь, не принявшая
задачу при отсутствии идущего прогона, — 503 `queue_unavailable`.
"""
from __future__ import annotations

import asyncio

from fastapi import Request, status
from fastapi.responses import FileResponse, JSONResponse, Response

from app.api._spend_gate import limit_gate, with_warning
from app.db import SessionLocal
from app.services import spend
from app.models import ScriptChapter
from app.v2.api import (
    _can_edit,
    _is_authenticated,
    _json_body_or_empty,
    bad_request_response,
    error_response,
    forbidden_response,
    not_found_response,
    unauthorized_response,
)

AMBIENT = "ambient"
#: длиннее правленого промпта ElevenLabs не бывает нужно; больше — это вставка не туда
MAX_PROMPT_CHARS = 2000


def _no_key() -> bool:
    from app.config import settings

    from app.services.provider_keys import provider_key

    return not provider_key("elevenlabs")


def _ambient_blocked(db, chapter) -> str:
    """Почему генерацию главы нельзя запустить сейчас: `already_running` | `not_ready` | ``."""
    from app.models import BackgroundRun
    from app.services.chapter_delivery import chapter_recording_status
    from app.services.consilium_engine import expire_dead_runs

    expire_dead_runs(db, chapter.id, kind=AMBIENT)
    running = (db.query(BackgroundRun)
               .filter(BackgroundRun.run_key == f"{AMBIENT}:{chapter.id}",
                       BackgroundRun.status.in_(("queued", "running"))).first())
    if running is not None:
        return "already_running"
    recording = chapter_recording_status(db, chapter.id)
    if recording is None or not recording["ready"]:
        return "not_ready"
    return ""


def _not_enqueued(chapter_id: str) -> JSONResponse:
    """Очередь вернула пусто: идёт прогон главы — 409, иначе очередь недоступна — 503."""
    from app.models import BackgroundRun

    with SessionLocal() as db:
        running = (db.query(BackgroundRun.id)
                   .filter(BackgroundRun.run_key == f"{AMBIENT}:{chapter_id}",
                           BackgroundRun.status.in_(("queued", "running"))).first())
    if running is not None:
        return error_response("already_running", status_code=409)
    return error_response("queue_unavailable", status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                          detail="Очередь недоступна, попробуйте позже")


def _refusal(code: str) -> JSONResponse:
    if code == "already_running":
        return error_response(code, status_code=409)
    return bad_request_response(code)


def api_v2_ambient_chapter(request: Request, chapter_id: str):
    """`POST /api/v2/chapters/{id}/ambient` — сгенерировать треки сценам главы без готового."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services.ambient_engine import ambient_plan, enqueue_ambient

    with SessionLocal() as db:
        chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
        if chapter is None:
            return not_found_response("chapter_not_found")
        if _no_key():
            return bad_request_response("no_key")
        blocked = _ambient_blocked(db, chapter)
        items = [] if blocked else ambient_plan(db, chapter.id)
        if not blocked and not items:
            blocked = "nothing_to_do"
        gate = None
        if not blocked:
            estimate, unknown = spend.estimate_ambient(db, items)
            gate = limit_gate(db, request, estimate_rub=estimate, unknown_price=unknown, what="эмбиент главы")
        found_id = chapter.id
        db.commit()  # мёртвый прогон, отмеченный проверкой, отмечается и в базе
    if gate is not None:
        return gate
    if blocked:
        return _refusal(blocked)
    run_id = enqueue_ambient(found_id)
    if not run_id:
        return _not_enqueued(found_id)
    return JSONResponse(with_warning(request, {"ok": True, "run_id": run_id}))


def api_v2_ambient_stop(request: Request, chapter_id: str):
    """`POST /api/v2/chapters/{id}/ambient/stop` — попросить генерацию главы остановиться.

    Идущий прогон доделывает текущий трек (он уже оплачивается) и встаёт перед следующим;
    ждущий в очереди — останавливается сразу, в том числе тот, чья задача потерялась и иначе
    держала бы кнопку закрытой. Близнец `api_v2_sound_stop`.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services.consilium_engine import request_stop

    with SessionLocal() as db:
        chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
        if chapter is None:
            return not_found_response("chapter_not_found")
        stopped = request_stop(db, chapter.id, kind=AMBIENT)
        db.commit()
    return JSONResponse({"ok": True, "stopped": stopped})


def api_v2_ambient_plan(request: Request, chapter_id: str):
    """`GET /api/v2/chapters/{id}/ambient/plan` — смета для подтверждения запуска: сколько
    треков сгенерирует кнопка, сколько это минут и сколько сцен без готового трека
    пропустит, потому что им нет места на таймлайне (`ambient_plan`). Раскладывает главу по
    таймлайну — поэтому своя ручка, а не поле строки ASR."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    import math

    from app.services.ambient_engine import _chapter_scenes, _done_markers

    with SessionLocal() as db:
        chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
        if chapter is None:
            return not_found_response("chapter_not_found")
        done = _done_markers(db, chapter.id)
        pending = [item for item in _chapter_scenes(db, chapter.id) if item["marker_id"] not in done]
    # Те же сцены, что отбирает `ambient_plan`: без готового трека и с местом на таймлайне.
    plan = [item for item in pending if item["span"] is not None]
    return JSONResponse({"ok": True, "tracks": len(plan),
                         "minutes": math.ceil(sum(item["seconds"] for item in plan) / 60),
                         "skipped": len(pending) - len(plan)})


async def api_v2_ambient_scene(request: Request, marker_id: str):
    """`POST /api/v2/sound/markers/{id}/ambient` — перегенерировать трек одной сцены.

    `prompt` в теле — правленый в карточке промпт, идёт в ElevenLabs как есть; пустой —
    новый промпт пишет Opus. Прежний готовый трек сцены получает `replaced`, файл остаётся.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    body = await _json_body_or_empty(request)
    if body is None:
        return bad_request_response()
    prompt = body.get("prompt", "")
    if prompt is None:
        prompt = ""
    if not isinstance(prompt, str) or len(prompt.strip()) > MAX_PROMPT_CHARS:
        return bad_request_response("bad_prompt")
    from app.models import SoundMarker
    from app.services.ambient_engine import _chapter_scenes, enqueue_ambient

    def check():
        with SessionLocal() as db:
            marker = db.get(SoundMarker, str(marker_id or "").strip())
            if marker is None or marker.status != "active":
                return None, "not_found"
            if marker.kind != "scene":
                return None, "not_scene"
            chapter = db.get(ScriptChapter, marker.chapter_id)
            if chapter is None:
                return None, "not_found"
            if _no_key():
                return None, "no_key"
            blocked = _ambient_blocked(db, chapter)
            if not blocked:
                scene = next((item for item in _chapter_scenes(db, chapter.id)
                              if item["marker_id"] == marker.id), None)
                # Сцене без места на таймлайне трек не ляжет в проект: прогон пропустил бы её
                # молча, а кнопка сказала бы «запущено».
                if scene is None or scene["span"] is None:
                    blocked = "not_placed"
                else:
                    # одна сцена — тоже новая платная работа: без проверки лимит обходился бы по сцене
                    # свой промпт — текстовая модель не нужна; иначе сцену опишут заново
                    estimate, unknown = spend.estimate_ambient(db, [{"seconds": scene["seconds"],
                                                                     "prompt": prompt.strip()}])
                    blocked = limit_gate(db, request, estimate_rub=estimate, unknown_price=unknown,
                                         what="эмбиент сцены") or ""
            db.commit()
            return (chapter.id, marker.id), blocked

    found, blocked = await asyncio.to_thread(check)
    if isinstance(blocked, JSONResponse):
        return blocked
    if blocked == "not_found":
        return not_found_response()
    if blocked:
        return _refusal(blocked)
    chapter_id, found_marker = found
    run_id = enqueue_ambient(chapter_id, marker_id=found_marker, prompt_override=prompt.strip())
    if not run_id:
        return await asyncio.to_thread(_not_enqueued, chapter_id)
    return JSONResponse(with_warning(request, {"ok": True, "run_id": run_id}))


def api_v2_ambient_audio(request: Request, audio_file_id: str):
    """`GET /api/v2/ambient/{id}/audio` — сам трек, для прослушивания в карточке сцены.

    Близнец `api_v2_take_audio`, и тоже с `Range` — иначе у плеера не двигается ползунок.
    Отдаёт только строки `kind='ambient'`: дубль или проба этой дверью недостижимы.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.models import AudioFile
    from app.services.audio_storage import file_size, read_range, resolve_path
    from app.services.auditions import inline_disposition, slice_for_range
    from app.v2.api import storage_unavailable_for

    with SessionLocal() as db:
        item = db.get(AudioFile, str(audio_file_id or "").strip())
        if item is None or str(item.kind or "") != AMBIENT:
            return not_found_response("ambient_not_found")
        key, mime, name = str(item.stored_key or ""), str(item.mime_type or "audio/mpeg"), str(item.canonical_filename or "")
        location = str(getattr(item, "location", None) or "local")
    if storage_unavailable_for(location):
        return JSONResponse({"ok": False, "error": "storage_unavailable"},
                            status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        total = file_size(key, location=location)
    except OSError:
        return not_found_response("audio_file_missing")

    headers = {"Accept-Ranges": "bytes", "Content-Disposition": inline_disposition(name)}
    window = slice_for_range(request.headers.get("range") or "", total)
    if window is None:
        return FileResponse(resolve_path(key, location=location), media_type=mime, headers=headers)
    start, end = window
    headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return Response(content=read_range(key, start, end - start + 1, location=location),
                    media_type=mime, status_code=206, headers=headers)
