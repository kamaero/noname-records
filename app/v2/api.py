"""HTTP surface of pipeline v2, wired into `app.main` directly like `app.api.asr_daw`.

Reading is open to anyone with a session: the actors who read the script are the
audience, and they have no workspace roles. Writing — a stress term, a profile sync —
is for editors, the `admin` and `author` roles; a dictor gets 403. The functions
take the request, check the cookie, open a session, and hand the work to the ops
modules; the only logic here is turning their results and errors into JSON.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from uuid import uuid4

from fastapi import Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask

from app.api._helpers import bad_request_response, error_response, forbidden_response, not_found_response, unauthorized_response
from app.auth import can_react_to_auditions, has_any_role, is_authenticated as _is_authenticated, session_payload, session_roles
from app.constants import DICTOR_ROLES
from app.api._spend_gate import limit_gate, with_warning
from app.db import SessionLocal
from app.services import spend
from app.constants import ChapterStatus
from sqlalchemy import func

from app.models import AudioFile, BackgroundRun, Character, ScriptBook, ScriptChapter, ScriptLog
from app.services.asr_replay import enqueue_realign_for_chapter
from app.services.audio_integrity import chapter_has_broken_files, enqueue_verify_for_chapter
from app.services.audio_storage import file_size as audio_size, read_range as read_audio_range, resolve_path as audio_path
from app.services.audio_naming import book_token
from app.services.audio_uploads import derive_book_code
from app.services.audition_reactions import ReactionError, attach_reactions, set_reaction
from app.services.auditions import book_auditions, find_audition, inline_disposition, slice_for_range
from app.services import book_title as book_title_service
from app.services.consilium_store import accept_finding, book_findings, dismiss_finding
from app.services.recording_impact import chapter_has_recognition, impacts_for_reassign, recording_impacts
from app.services.chapter_delivery import (
    archive_chapter_session,
    book_recording_status,
    build_chapter_archive,
    build_chapter_session,
    chapter_recording_status,
    find_take,
    StorageUnavailable,
)
from app.time_utils import iso_utc, utcnow_naive
from app.v2.attribution_ops import ReassignError, reassign_segment
from app.v2 import model_catalog
from app.v2.cast_ops import CreateRoleError, book_cast, create_role
from app.v2.character_map import (
    CharacterMapError,
    adopt_speaker,
    character_map,
    delete_character,
    merge_speaker,
    rename_role,
    update_role,
)
from app.v2.dispute_ops import disputed_spans
from app.v2.homograph_ops import homograph_places, homograph_words
from app.v2.models import V2Run, V2Segment
from app.v2.pipeline import STEPS, V2_MODE, active_run, create_queued_run, expire_stale_runs
from app.v2.profile_ops import apply_from_author, bind_author, book_profile, sync_to_author
from app.v2.progress import book_progress
from app.v2.publish_ops import publish_book, unpublish_book
from app.v2.reader import build_book_chapters, build_chapter_payload
from app.v2.review_ops import approve_all, set_chapter_approved
from app.v2.role_intersections import acknowledge_pair, forget_pair, role_intersections
from app.v2.role_view import role_script, role_traps
from app.v2.source_view import build_source_view
from app.v2.store import record_operator_intervention
from app.v2.stress_ops import (
    StressTermError,
    save_stress_term,
    set_place_stress,
    skip_stress_word,
    stress_queue,
    unskip_stress_word,
)
from app.workers.launcher import enqueue_tracked_task

logger = logging.getLogger(__name__)

# Two capabilities, not one. A dictor works on stress and on the palette of the roles
# he reads — that is his craft — and does not start runs, publish, approve or touch
# money. One `_can_edit` used to guard all of it together, so he could do none of it.
EDITOR_ROLES = {"author"}  # `admin` is implied by has_any_role
VOICE_ROLES = {"author", *DICTOR_ROLES}
V2_TASK = "app.worker_tasks.perform_v2_pipeline_task"
ASR_TASK = "app.worker_tasks.perform_asr_chapter_task"
V2_QUEUE = "high"


def v2_run_key(book_id: str) -> str:
    return f"v2-pipeline:{book_id}"


def _can_edit(request: Request) -> bool:
    """The markup and the pipeline: runs, publishing, roles, the author's profile."""
    return has_any_role(request, EDITOR_ROLES)


def _chapter_is_published(db, chapter_id: str) -> bool:
    """Опубликована ли глава. Несуществующая — тоже «нет»: ответ один и тот же."""
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    return chapter is not None and str(chapter.status or "") == ChapterStatus.PUBLISHED.value


def _can_voice(request: Request) -> bool:
    """Stress and palette — what a dictor needs to read a book aloud."""
    return has_any_role(request, VOICE_ROLES)


def _reader_name(request: Request) -> str:
    """Who is reading, for «мои роли». Unlike `_actor` there is no «operator» fallback:
    a session without a name owns no roles, and a placeholder would match a real actor."""
    return str(session_payload(request).get("display_name") or "").strip()


def _actor(request: Request) -> tuple[str, str]:
    payload = session_payload(request)
    uid = str(payload.get("uid") or "operator")
    name = str(payload.get("display_name") or payload.get("sub") or "").strip() or "operator"
    return uid, name


async def _json_body(request: Request) -> dict | None:
    try:
        body = await request.json()
    except ValueError:  # не JSON (в т.ч. не UTF-8) — это плохой запрос, а не сбой
        return None
    return body if isinstance(body, dict) else None


async def _json_body_or_empty(request: Request) -> dict | None:
    """Like `_json_body`, but a request with no body at all means «defaults»."""
    raw = await request.body()
    if not raw.strip():
        return {}
    return await _json_body(request)


def api_v2_chapter_script(request: Request, chapter_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    can_edit = _can_edit(request)
    with SessionLocal() as db:
        if not can_edit and not _chapter_is_published(db, chapter_id):
            # Интерфейс диктора и так уводит его из «Подготовки», но это редирект на
            # клиенте: маршрут перестал открываться, а ручка отвечала по-прежнему.
            # Неопубликованная глава ещё правится автором, и реплика из неё может
            # исчезнуть или сменить роль — записавший её потратит время впустую.
            return not_found_response("chapter_not_published")
        payload = build_chapter_payload(
            db, str(chapter_id or "").strip(),
            can_edit=can_edit, can_voice=_can_voice(request),
        )
    if payload is None:
        return not_found_response()
    return JSONResponse(payload)


def api_v2_chapter_source(request: Request, chapter_id: str):
    """The chapter's original text, blocked to line up with the script pane."""
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        # Тот же текст, что и в сценарии, только другой стороной: оставить эту ручку
        # открытой значило бы поставить шлюз с открытой калиткой рядом.
        if not _can_edit(request) and not _chapter_is_published(db, chapter_id):
            return not_found_response("chapter_not_published")
        payload = build_source_view(db, str(chapter_id or "").strip())
    if payload is None:
        return not_found_response()
    return JSONResponse(payload)


def api_v2_book_chapters(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        payload = build_book_chapters(db, str(book_id or "").strip())
    if payload is None:
        return not_found_response()
    return JSONResponse(payload)


def api_v2_disputed(request: Request, book_id: str):
    """Lines the model would not name a speaker for, or named with a hedge."""
    if not _is_authenticated(request):
        return unauthorized_response()
    # Очередь собирается по всем главам, опубликованным и нет, — это инструмент
    # редактора. Интерфейс и так просит её только у него; ручка думает так же.
    if not _can_edit(request):
        return forbidden_response("read_only")
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        payload = disputed_spans(db, book.id)
    return JSONResponse({"ok": True, **payload})


def api_v2_consilium(request: Request, book_id: str):
    """Находки консилиума по книге — сводка и список, при желании по одной главе."""
    if not _is_authenticated(request):
        return unauthorized_response()
    chapter = request.query_params.get("chapter")
    with SessionLocal() as db:
        # Опечатка в id не должна выглядеть как «находок нет»: пустой список — самый
        # успокоительный из ответов, и ровно тот, которого опечатка не заслуживает.
        if db.get(ScriptBook, str(book_id or "").strip()) is None:
            return not_found_response("book_not_found")
        payload = book_findings(
            db, book_id=str(book_id or ""),
            chapter_index=int(chapter) if str(chapter or "").strip().isdigit() else None,
        )
        # Деньги (spent_rub/estimate_rub) внутри `run` — не для диктора: спецификация
        # прямо запрещает показывать ему баланс и кнопку запуска.
        if _can_edit(request):
            from app.services.consilium_engine import latest_run
            payload["run"] = latest_run(db, str(book_id or "").strip())
            db.commit()  # мёртвый прогон, отмеченный при чтении, отмечается и в базе
        else:
            payload["run"] = None
    return JSONResponse({"ok": True, **payload})


def _consilium_blocked(db, book, *, credits, estimate_rub) -> str:
    from app.models import BackgroundRun
    from app.services.consilium_engine import BUSY_STATUSES, MIN_CREDITS_RUB, expire_dead_runs

    # Прогон, час не подававший признаков жизни, мёртв: он не держит кнопки. Фиксирует вызывающий.
    expire_dead_runs(db, book.id)
    running = (db.query(BackgroundRun)
               .filter(BackgroundRun.run_key == f"consilium:{book.id}",
                       BackgroundRun.status.in_(("queued", "running"))).first())
    if running is not None:
        return "already_running"
    if str(book.status or "") in BUSY_STATUSES:
        return "book_busy"
    if credits is not None and credits < max(MIN_CREDITS_RUB, estimate_rub):
        return "no_credits"
    return ""


def api_v2_consilium_estimate(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services import consilium_engine

    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        plan = consilium_engine.estimate(db, book.id, root=consilium_engine.ARTIFACT_ROOT)
        credits = consilium_engine.read_credits()
        blocked = {mode: _consilium_blocked(db, book, credits=credits, estimate_rub=plan["estimate_rub"][mode])
                   for mode in ("recheck", "reread")}
        db.commit()
    return JSONResponse({"ok": True, **plan, "credits": credits, "blocked": blocked})


async def api_v2_consilium_run(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    body = await _json_body_or_empty(request)
    mode = str((body or {}).get("mode") or "")
    if mode not in ("recheck", "reread"):
        return bad_request_response("bad_mode")
    from app.services import consilium_engine

    # Смета большой книги — секунды базы и процессора; на цикле событий она морозила бы весь
    # сайт. Поэтому всё, что трогает базу, — в потоке, и сессия живёт только в нём.
    def plan_and_check():
        with SessionLocal() as db:
            book = db.get(ScriptBook, str(book_id or "").strip())
            if book is None:
                return None, None, ""
            plan = consilium_engine.estimate(db, book.id, root=consilium_engine.ARTIFACT_ROOT)
            if plan["chapters_total"] == 0:
                return book.id, plan, ""
            credits = consilium_engine.read_credits()
            blocked = _consilium_blocked(db, book, credits=credits,
                                         estimate_rub=plan["estimate_rub"][mode])
            gate = None if blocked else limit_gate(
                db, request, estimate_rub=plan["estimate_rub"][mode], what="консилиум",
                unknown_price=spend.unknown_for(db, ("consilium_reader_1", "consilium_reader_2", "consilium_arbiter")))
            db.commit()
            return book.id, plan, blocked or gate

    found_id, plan, blocked = await asyncio.to_thread(plan_and_check)
    if isinstance(blocked, JSONResponse):
        return blocked
    if found_id is None:
        return not_found_response("book_not_found")
    if plan["chapters_total"] == 0:
        return bad_request_response("no_markup")
    if blocked in ("already_running", "book_busy"):
        return error_response(blocked, status_code=409)
    if blocked:
        return bad_request_response(blocked)
    run_id = consilium_engine.enqueue_consilium(found_id, mode)
    if not run_id:
        return error_response("already_running", status_code=409)
    return JSONResponse(with_warning(request, {"ok": True, "run_id": run_id}))


def api_v2_consilium_stop(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services.consilium_engine import request_stop

    with SessionLocal() as db:
        stopped = request_stop(db, str(book_id or "").strip())
        db.commit()
    return JSONResponse({"ok": True, "stopped": stopped})


def chapter_of_segment(db, segment_id: str) -> str:
    segment = db.get(V2Segment, str(segment_id or "").strip())
    return str(segment.chapter_id) if segment is not None else ""


def _queue_realign(chapter_id: str) -> None:
    """Пересчитать сверку записанной главы после правки роли. Сбой очереди правку не отменяет."""
    try:
        enqueue_realign_for_chapter(chapter_id)
    except Exception:
        logger.exception("Не удалось поставить пересчёт сверки главы %s", chapter_id)


async def _decide_finding(request: Request, finding_id: str, decide) -> JSONResponse:
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    body = await _json_body_or_empty(request)
    if body is None:
        return bad_request_response()
    # `uid`, а не `sub`: в журнал вмешательств ложится id пользователя, и весь
    # остальной v2 кладёт туда его же. Логин в этой графе выглядит как id и тихо рвёт
    # связь строки журнала с человеком.
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        try:
            out = decide(db, finding_id=str(finding_id or ""),
                         actor_uid=actor_uid, actor_name=actor_name, body=body)
        except ValueError as exc:
            return bad_request_response(str(exc))
        db.commit()
        chapter_id = chapter_of_segment(db, str(out.get("segment_id") or "")) if out.get("status") == "accepted" else ""
        recorded = bool(chapter_id) and chapter_has_recognition(db, chapter_id)
    if recorded:
        _queue_realign(chapter_id)
    return JSONResponse({"ok": True, **out})


async def api_v2_consilium_accept(request: Request, finding_id: str):
    def decide(db, *, body, **kw):
        # Ключа нет — прежний контракт (решает арбитр); ключ есть — имя назвал человек.
        # `{"speaker": null}` — тоже «ключ есть»: после разбора тела `null` и «ключа не
        # было» одинаково превращаются в `None`, а по смыслу запроса это разное. Не
        # различая их, ручка тихо откатилась бы на вердикт арбитра там, где кто-то как
        # раз пытался назвать имя — `accept_finding` обязан ответить `unknown_speaker`,
        # а не молча решить за арбитра.
        speaker = str(body["speaker"] or "") if "speaker" in body else None
        return accept_finding(db, speaker=speaker, **kw)
    return await _decide_finding(request, finding_id, decide)


async def api_v2_consilium_dismiss(request: Request, finding_id: str):
    def decide(db, *, body, **kw):
        return dismiss_finding(db, **kw)
    return await _decide_finding(request, finding_id, decide)


def api_v2_consilium_recording_impact(request: Request, finding_id: str):
    """`GET /api/v2/consilium/{id}/recording-impact` — что станет со звуком при каждом имени каста."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.models import ConsiliumFinding
    from app.v2.attribution_ops import NARRATOR, _cast_names

    with SessionLocal() as db:
        row = db.get(ConsiliumFinding, str(finding_id or "").strip())
        if row is None:
            return not_found_response("finding_not_found")
        names = [NARRATOR, *sorted(set(_cast_names(db, row.book_id).values()))]
        out = recording_impacts(db, segment_id=row.segment_id, span_start=int(row.span_start),
                                span_end=int(row.span_end), to_roles=names)
    return JSONResponse({"ok": True, **out})


def api_v2_stress_queue(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        params = request.query_params
        payload = stress_queue(
            db,
            book.id,
            scope=str(params.get("scope") or "all"),
            limit=max(1, min(5000, int(str(params.get("limit") or 200) or 200))),
            offset=max(0, int(str(params.get("offset") or 0) or 0)),
        )
    return JSONResponse({"ok": True, **payload})


# What a role is: what the pipeline guessed (`race`, `age`, `temperament`) and what the
# author says about the voice (`operator_note`). The guess is a draft, not a verdict,
# so all four are editable — a field left out of the body is left alone, because the
# screen edits one line at a time.
_ABOUT_FIELDS = {
    "race": ("race", 255),
    "age": ("age", 120),
    "temperament": ("temperament", 2000),
    "note": ("operator_note", 2000),
}


async def api_v2_character_about(request: Request, character_id: str):
    """Who the character is and how he should sound. Dictors read it; they do not write it."""
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    _uid, actor_name = _actor(request)
    with SessionLocal() as db:
        row = db.get(Character, str(character_id or "").strip())
        if row is None:
            return not_found_response("character_not_found")
        changed = []
        for key, (column, limit) in _ABOUT_FIELDS.items():
            if key not in body:
                continue
            value = str(body.get(key) or "").strip()[:limit]
            if value != str(getattr(row, column, "") or ""):
                setattr(row, column, value)
                changed.append(key)
        if changed:
            db.add(ScriptLog(book_id=row.book_id, level="info",
                             message=f"Каст: {actor_name} изменил роль «{row.name}» ({', '.join(changed)})."))
        payload = {
            "character_id": row.id,
            "name": row.name,
            "race": str(row.race or ""),
            "age": str(row.age or ""),
            "temperament": str(row.temperament or ""),
            "note": str(row.operator_note or ""),
        }
        db.commit()
    return JSONResponse({"ok": True, "character": payload, "note": payload["note"]})


def api_v2_book_recording(request: Request, book_id: str):
    """Сколько ролей записано в каждой главе книги — и какие главы можно забирать."""
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        # Эмбиент — только редактору: дикторам он не показан.
        status = book_recording_status(db, str(book_id or "").strip(), ambient=_can_edit(request))
    if status is None:
        return not_found_response("book_not_found")
    return JSONResponse({"ok": True, **status, "can_download": _can_edit(request)})


def api_v2_chapter_recording(request: Request, chapter_id: str):
    """Кто из говорящих ролей главы уже записан, а кто ещё нет.

    Открыто всем: диктору полезно видеть, что глава ждёт только его, — это точнее
    любого напоминания.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        status = chapter_recording_status(db, str(chapter_id or "").strip())
    if status is None:
        return not_found_response("chapter_not_found")
    return JSONResponse({"ok": True, **status, "can_download": _can_edit(request)})


def api_v2_chapter_session(request: Request, chapter_id: str):
    """Сессия Audition для главы: треки по ролям, клипы по репликам.

    Файл маленький — это XML со ссылками на аудио, а не само аудио. Пути в нём
    абсолютные: Audition открывает сессию на той машине, где лежат файлы.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    with SessionLocal() as db:
        xml = build_chapter_session(db, str(chapter_id or "").strip())
        status = chapter_recording_status(db, str(chapter_id or "").strip())
    if xml is None or status is None:
        return not_found_response("chapter_not_found")
    name = f"{status['chapter_index']:02d}.sesx"
    return Response(
        content=xml.encode("utf-8"),
        media_type="application/xml",
        headers={"Content-Disposition": inline_disposition(name).replace("inline;", "attachment;")},
    )


def api_v2_chapter_asr(request: Request, chapter_id: str):
    """Поставить главу в очередь на распознавание.

    В фоне, а не в запросе: минута машинного времени на файл — и это только сжатие с
    отправкой. Браузер столько не ждёт, а ждать заставлять незачем.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    with SessionLocal() as db:
        status = chapter_recording_status(db, str(chapter_id or "").strip())
    if status is None:
        return not_found_response("chapter_not_found")
    if not any(row["files"] for row in status["roles"]):
        return error_response("nothing_recorded", status_code=409)

    job_id = enqueue_tracked_task(
        queue_name=V2_QUEUE,
        func_ref=ASR_TASK,
        session_factory=SessionLocal,
        background_run_cls=BackgroundRun,
        job_kind="asr_chapter",
        entity_type="script_chapter",
        entity_id=str(chapter_id or "").strip(),
        run_key=f"asr:{chapter_id}",
        meta={"chapter_id": str(chapter_id or "").strip(), "force": True},
        chapter_id=str(chapter_id or "").strip(),
        # Кнопка обещает распознать заново — и платит за это; приём файлов ставит ту же
        # задачу без `force` и пересчитывает уже услышанное бесплатно.
        force=True,
    )
    if not job_id:
        return error_response("already_queued", status_code=409)
    return JSONResponse({"ok": True, "job_id": job_id})


def api_v2_chapter_verify(request: Request, chapter_id: str):
    """Сверить главу по кнопке. Ответ — сразу, работа — в фоне.

    `None` от дедупликации — сверка уже идёт, а не «не запустилась»: зеркалим
    соседнюю ручку распознавания, у которой та же ситуация отвечает 409, а не
    молчаливым «ok: true».

    Право то же, что у соседних ручек: сверка — это многоминутное перечитывание главы
    с локального диска сервера в единственной очереди `high`, которую она делит с
    распознаванием. Без `_can_edit` любой диктор мог занять её на всю свою смену.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    job_id = enqueue_verify_for_chapter(str(chapter_id or "").strip())
    if not job_id:
        return error_response("already_queued", status_code=409)
    return JSONResponse({"ok": True, "job_id": job_id})


def api_v2_chapter_session_archive(request: Request, chapter_id: str):
    """`POST /api/v2/chapters/{id}/session-archive` — положить .sesx на NAS."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    with SessionLocal() as db:
        result = archive_chapter_session(db, str(chapter_id or "").strip())
        if result is None:
            return not_found_response("chapter_not_found")
        db.commit()
    if result["written"]:
        return JSONResponse({"ok": True, "replaced": result["replaced"]})
    # "unchanged" — не отказ: сессия уже на NAS и соответствует текущей сверке,
    # нажатие кнопки просто застало главу без правок с прошлого раза.
    if result["reason"] == "unchanged":
        return JSONResponse({"ok": True, "unchanged": True})
    return JSONResponse({"ok": False, "error": result["reason"]}, status_code=status.HTTP_409_CONFLICT)


def api_v2_chapter_archive(request: Request, chapter_id: str):
    """Дубли главы одним архивом — исходник для сведения.

    Собирается на диск и отдаётся файлом: глава весит сотни мегабайт, держать её в
    памяти незачем. Временный архив удаляется после отдачи.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    target = os.path.join(tempfile.gettempdir(), f"noname-chapter-{uuid4().hex}.zip")
    with SessionLocal() as db:
        if chapter_has_broken_files(db, str(chapter_id or "").strip()):
            # Клеить сессию для монтажки из файлов, про которые известно, что они
            # битые, — значит отдать эту находку звукорежиссёру вместо владельца.
            return JSONResponse(
                {"ok": False, "error": "integrity_failed"},
                status_code=status.HTTP_409_CONFLICT,
            )
        try:
            result = build_chapter_archive(db, str(chapter_id or "").strip(), target)
        except StorageUnavailable:
            # Часть дублей на NAS, а он молчит: тот же быстрый отказ, что у прослушивания.
            return JSONResponse(
                {"ok": False, "error": "storage_unavailable"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        # Сборка помечает главу сданной — без фиксации отметка уходит вместе с сессией.
        db.commit()
    if result is None:
        if os.path.exists(target):
            os.remove(target)
        return not_found_response("chapter_not_found")
    return FileResponse(
        target,
        media_type="application/zip",
        filename=str(result.get("archive_name") or "chapter.zip"),
        background=BackgroundTask(lambda: os.path.exists(target) and os.remove(target)),
    )


def api_v2_book_auditions(request: Request, book_id: str):
    """Пробы этой книги — слушают все, утверждают на роль двое.

    Услышать, как роль звучит в чужом исполнении, полезно каждому, кто книгу читает.
    Решение — чей это голос — принимают владелец и автор, и `can_approve` не просто
    прячет кнопку: по этому же признаку охраняется само назначение актёра.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        book_id = str(book_id or "").strip()
        rows = book_auditions(db, book_id=book_id)
        if rows is not None:
            rows = attach_reactions(
                db, rows, book_id=book_id, viewer_name=_reader_name(request), sees_all=_can_edit(request),
            )
    if rows is None:
        return not_found_response("book_not_found")
    return JSONResponse({
        "ok": True, "items": rows, "can_approve": _can_edit(request),
        "can_react": can_react_to_auditions(request),
    })


def storage_unavailable_for(location: str) -> bool:
    """Отказать ли в прослушивании, не пытаясь читать файл.

    Приём переехал на диск сервера, и для локального файла сторож не нужен:
    молчащий NAS ему не помеха, а отказ из-за домашнего канала владельца был бы
    выдумкой. Но снять сторож совсем нельзя, пока живы записи, физически лежащие
    на NAS, — у них `location` так и говорит, и до переезда таких шестьдесят.
    Обращение к мёртвому `hard`-монтированию не падает, а виснет навсегда, и
    каждый такой запрос съедает поток. Быстрый отказ честнее повисшей вкладки.

    Читаем только на подтверждённом «жив». «Не знаем» раньше пускали на NFS, чтобы
    рестарт не гасил прослушивание до первой пробы, — 27.09 так кончились потоки и
    лёг сайт. Первая проба приходит за секунды после старта.

    Ветка умрёт сама, когда переезд проставит старым записям `location = local`.
    """
    from app.services import nas_health

    if str(location or "local") != "nas":
        return False
    return nas_health.nas_online() is not True


async def api_v2_audition_reaction(request: Request, audio_id: str):
    """👍/👎 на пробу. Ставит только автор — не «редактор»: `admin` тут не подразумевается,
    потому что реакция — голос именно автора, а за 👎 следует письмо диктору."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not can_react_to_auditions(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    uid, name = _actor(request)
    try:
        value = int(body.get("value"))
    except (TypeError, ValueError):
        return bad_request_response("bad_value")
    with SessionLocal() as db:
        try:
            result = set_reaction(db, str(audio_id or "").strip(), voter_uid=uid, voter_name=name, value=value)
        except ReactionError as error:
            if error.code == "not_found":
                return not_found_response("audition_not_found")
            return bad_request_response(error.code)
    return JSONResponse({"ok": True, **result})


def api_v2_audition_audio(request: Request, audio_id: str):
    """Сама запись. Отдаётся с поддержкой `Range` — иначе у плеера не двигается ползунок."""
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        item = find_audition(db, str(audio_id or "").strip())
        if item is None:
            return not_found_response("audition_not_found")
        key, mime, name = str(item.stored_key or ""), str(item.mime_type or "audio/wav"), str(item.canonical_filename or "")
        location = str(getattr(item, "location", None) or "local")
    if storage_unavailable_for(location):
        return JSONResponse(
            {"ok": False, "error": "storage_unavailable"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        total = audio_size(key, location=location)
    except OSError:
        return not_found_response("audio_file_missing")

    headers = {"Accept-Ranges": "bytes", "Content-Disposition": inline_disposition(name)}
    window = slice_for_range(request.headers.get("range") or "", total)
    if window is None:
        # отдаётся с диска потоком: проба весит десятки мегабайт
        return FileResponse(audio_path(key, location=location), media_type=mime, headers=headers)
    start, end = window
    headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return Response(
        content=read_audio_range(key, start, end - start + 1, location=location),
        media_type=mime,
        status_code=206,
        headers=headers,
    )


def api_v2_take_audio(request: Request, audio_id: str):
    """Дубль главы: послушать в описи или забрать файлом.

    Близнец `api_v2_audition_audio` — и по той же причине отвечает на `Range`: дубль
    главы бывает и вдвое тяжелее пробы, а без ответа на кусок ползунок в плеере не
    двигается и запись можно слушать только с начала.

    Право — только «свой человек», и это нарочно: дубли коллег слушают и забирают все,
    кто книгу читает. Стирает их не каждый, но стирание живёт в своей ручке
    (`/api/recording/files/{id}/delete`) и со своим правом — здесь его нет вовсе.

    `?download=1` меняет только заголовок: одна дверь, а не вторая копия логики.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        item = find_take(db, str(audio_id or "").strip())
        if item is None:
            return not_found_response("take_not_found")
        key, mime, name = str(item.stored_key or ""), str(item.mime_type or "audio/wav"), str(item.canonical_filename or "")
        location = str(getattr(item, "location", None) or "local")
    if storage_unavailable_for(location):
        return JSONResponse(
            {"ok": False, "error": "storage_unavailable"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        total = audio_size(key, location=location)
    except OSError:
        return not_found_response("audio_file_missing")

    disposition = inline_disposition(name)
    if str(request.query_params.get("download") or "").strip():
        disposition = disposition.replace("inline;", "attachment;")
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": disposition}
    window = slice_for_range(request.headers.get("range") or "", total)
    if window is None:
        # отдаётся с диска потоком: дубль главы весит десятки и сотни мегабайт
        return FileResponse(audio_path(key, location=location), media_type=mime, headers=headers)
    start, end = window
    headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return Response(
        content=read_audio_range(key, start, end - start + 1, location=location),
        media_type=mime,
        status_code=206,
        headers=headers,
    )


def api_v2_homographs(request: Request, book_id: str):
    """«Спорные ударения»: what the model chose where two readings were possible.

    Without `?word=` — one row per ambiguous word, the most doubtful first. With it —
    every place of that word, the rare reading first, because that is where a mistake
    hides.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    word = str(request.query_params.get("word") or "").strip()
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        if word:
            return JSONResponse({"ok": True, "word": word, "places": homograph_places(db, book.id, word)})
        rows = homograph_words(db, book.id)
    return JSONResponse({
        "ok": True,
        "words": rows,
        "counts": {
            "words": len(rows),
            "places": sum(row["places"] for row in rows),
            "mixed_words": sum(1 for row in rows if row["mixed"]),
            "rare_places": sum(row["rare_places"] for row in rows),
        },
    })


async def api_v2_segment_stress(request: Request, segment_id: str):
    """One place, one reading — the decision a homograph needs and a rule cannot give."""
    if not _can_voice(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    uid, actor_name = _actor(request)
    with SessionLocal() as db:
        try:
            result = set_place_stress(
                db,
                segment_id=segment_id,
                word_start=int(body.get("word_start") or 0),
                word_end=int(body.get("word_end") or 0),
                vowel_offset=int(body.get("vowel_offset") or 0),
                actor_uid=uid,
            )
        except StressTermError as exc:
            return bad_request_response(exc.code)
        except (TypeError, ValueError):
            return bad_request_response("bad_payload")
        segment = db.get(V2Segment, segment_id)
        if segment is not None:
            db.add(ScriptLog(book_id=segment.book_id, level="info",
                             message=f"Ударения: {actor_name} выбрал чтение «{result['word']}» в одном месте."))
        db.commit()
    return JSONResponse({"ok": True, **result})


_PALETTE_FIELDS = {
    "character_color": 20,
    "character_text_color": 20,
    "character_font_weight": 20,
    "character_font_style": 20,
}


async def api_v2_character_palette(request: Request, character_id: str):
    """Colours only.

    The palette used to be saved by the budget endpoint, next to rates and totals, so
    opening it to dictors would have opened the money with it. A role's colour is what
    a dictor reads by — it belongs to him — and a rate does not.
    """
    if not _can_voice(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    _uid, actor_name = _actor(request)
    with SessionLocal() as db:
        row = db.get(Character, str(character_id or "").strip())
        if row is None:
            return not_found_response("character_not_found")
        changed = False
        for field, limit in _PALETTE_FIELDS.items():
            if field not in body:
                continue
            value = str(body.get(field) or "").strip()[:limit]
            if value != str(getattr(row, field, "") or ""):
                setattr(row, field, value)
                changed = True
        if changed:
            db.add(ScriptLog(book_id=row.book_id, level="info",
                             message=f"Каст: {actor_name} изменил цвет роли «{row.name}»."))
        payload = {field: str(getattr(row, field, "") or "") for field in _PALETTE_FIELDS}
        db.commit()
    return JSONResponse({"ok": True, "character_id": str(character_id), **payload})


async def api_v2_stress_skip(request: Request, book_id: str):
    """«Ударение тут не нужно» — the word leaves this book's queue, or comes back."""
    if not _can_voice(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    word = str(body.get("word") or "")
    if not word.strip():
        return bad_request_response("word_required")
    uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        if bool(body.get("undo")):
            changed = unskip_stress_word(db, book_id=book.id, word=word)
            action = "вернул в очередь"
        else:
            changed = skip_stress_word(db, book_id=book.id, word=word, actor_uid=uid)
            action = "убрал из очереди"
        if changed:
            db.add(ScriptLog(book_id=book.id, level="info",
                             message=f"Ударения: {actor_name} {action} слово «{word.strip()}»."))
        db.commit()
    return JSONResponse({"ok": True, "changed": changed})


async def api_v2_stress_term(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_voice(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        try:
            result = save_stress_term(
                db, book=book,
                word=str(body.get("word") or ""),
                stressed=str(body.get("stressed") or ""),
                scope=str(body.get("scope") or "book"),
                actor_uid=actor_uid, actor_name=actor_name,
            )
        except StressTermError as exc:
            return bad_request_response(exc.code)
        db.commit()
    return JSONResponse({"ok": True, **result})


def api_v2_character_map(request: Request, book_id: str):
    """Карта персонажей вместе с разметкой — рабочий стол автора, не диктора.

    Пересечения ролей — та же карта, только про пары актёров, а не про
    отдельную строку, поэтому отдаются вместе одним запросом: экран рисует их
    рядом, и заводить для этого второй маршрут незачем. Отметка «автор
    проверил» — на книге, а не на роли: по ней режиссёр судит про карту
    целиком.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    with SessionLocal() as db:
        book_id = str(book_id or "").strip()
        rows = character_map(db, book_id)
        intersections = role_intersections(db, book_id)
        book = db.get(ScriptBook, book_id)
        checked_at = (iso_utc(book.cast_checked_at) if book is not None else None) or ""
        checked_by = str(book.cast_checked_by or "") if book is not None else ""
    return JSONResponse({
        "ok": True, "rows": rows, "intersections": intersections,
        "checked_at": checked_at, "checked_by": checked_by,
    })


async def api_v2_character_map_delete(request: Request, book_id: str):
    """Убрать молчащего фантома из карты. Говорящих и записанных не трогает.

    Строка несёт диктора, цвет, алиасы и связь с каноном автора, а версии
    атрибуций этого не хранят — потому удаление тоже пишет в журнал
    вмешательств, содержимым, а не только «удалили X».
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    uid, actor_name = _actor(request)
    with SessionLocal() as db:
        try:
            result = delete_character(
                db, str(book_id or "").strip(), str(body.get("name") or ""),
                actor_uid=uid, actor_name=actor_name,
            )
        except CharacterMapError as error:
            return bad_request_response(error.code)
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_character_map_adopt(request: Request, book_id: str):
    """Завести в карте имя, которое уже говорит в разметке."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    with SessionLocal() as db:
        try:
            result = adopt_speaker(db, str(book_id or "").strip(), str(body.get("name") or ""))
        except CharacterMapError as error:
            return bad_request_response(error.code)
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_character_map_merge(request: Request, book_id: str):
    """Переклеить реплики одного имени на другое и убрать исходное из карты.

    Единственная операция таблицы, трогающая разметку по всем главам сразу —
    и потому у неё, как и у удаления, в журнале записано не только «слили X в
    Y», а всё, что несла строка карты источника: диктор, цвет, алиасы, канон.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    uid, actor_name = _actor(request)
    with SessionLocal() as db:
        try:
            result = merge_speaker(
                db, book_id=str(book_id or "").strip(),
                source=str(body.get("source") or ""), target=str(body.get("target") or ""),
                actor_uid=uid, actor_name=actor_name,
            )
        except CharacterMapError as error:
            return bad_request_response(error.code)
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_character_map_rename(request: Request, book_id: str):
    """Переименовать роль. Согласие на переезд реплик приходит вторым заходом:
    первый отвечает `needs_consent`, и экран называет цену словами."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    uid, actor_name = _actor(request)
    with SessionLocal() as db:
        try:
            result = rename_role(
                db, book_id=str(book_id or "").strip(),
                name=str(body.get("name") or ""), new_name=str(body.get("new_name") or ""),
                move_lines=bool(body.get("move_lines")),
                actor_uid=uid, actor_name=actor_name,
            )
        except CharacterMapError as error:
            return bad_request_response(error.code)
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_character_map_update(request: Request, book_id: str):
    """Поменять поля роли: расу, возраст, характер, голос, алиасы, диктора."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    with SessionLocal() as db:
        try:
            result = update_role(
                db, str(book_id or "").strip(), str(body.get("name") or ""),
                body.get("fields") or {},
            )
        except CharacterMapError as error:
            return bad_request_response(error.code)
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_character_map_acknowledge(request: Request, book_id: str):
    """Подтвердить, что пересечение пары ролей одного актёра — так задумано."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    uid, actor_name = _actor(request)
    with SessionLocal() as db:
        result = acknowledge_pair(
            db, book_id=str(book_id or "").strip(),
            role_a=str(body.get("role_a") or ""), role_b=str(body.get("role_b") or ""),
            reason=str(body.get("reason") or ""),
            actor_uid=uid, actor_name=actor_name,
        )
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_character_map_forget(request: Request, book_id: str):
    """Отменить подтверждение пары — предупреждение про пересечение вернётся."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    with SessionLocal() as db:
        result = forget_pair(
            db, book_id=str(book_id or "").strip(),
            role_a=str(body.get("role_a") or ""), role_b=str(body.get("role_b") or ""),
        )
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_character_map_checked(request: Request, book_id: str):
    """Отметить, что автор проверил карту персонажей целиком.

    Отметка живёт у книги, а не у отдельной роли: правка любого поля карты
    её не снимает — это осознанное решение человека, а не техническое
    состояние, которое можно потерять по ошибке.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response("bad_json")
    _, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        book.cast_checked_at = utcnow_naive()
        book.cast_checked_by = actor_name
        db.commit()
        checked_at = iso_utc(book.cast_checked_at)
        checked_by = book.cast_checked_by
    return JSONResponse({"ok": True, "checked_at": checked_at, "checked_by": checked_by})


def api_v2_cast(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        characters = book_cast(db, book.id, actor_name=_reader_name(request))
    return JSONResponse({"ok": True, "book_id": book.id, "characters": characters})


def api_v2_profile(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        payload = book_profile(db, str(book_id or "").strip())
    if payload is None:
        return not_found_response("book_not_found")
    return JSONResponse({"ok": True, **payload})


async def api_v2_profile_sync(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        summary = sync_to_author(db, book, actor_uid, actor_name)
        db.commit()
    return JSONResponse({"ok": True, "summary": summary})


async def api_v2_profile_apply(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        summary = apply_from_author(db, book, overwrite=bool(body.get("overwrite")), actor_uid=actor_uid, actor_name=actor_name)
        db.commit()
    return JSONResponse({"ok": True, "summary": summary})


async def api_v2_bind_author(request: Request, book_id: str):
    """Point the book at an author — and pull that author's cast down onto it."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        try:
            result = bind_author(
                db, book=book, author_id=str(body.get("author_id") or ""),
                actor_uid=actor_uid, actor_name=actor_name,
            )
        except ValueError as exc:
            return bad_request_response(str(exc))
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_set_model(request: Request, book_id: str):
    """Point the book at one of the catalogued models. The next run uses it."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    key = str(body.get("model_key") or "").strip()
    if key not in model_catalog.BY_KEY:
        return bad_request_response("unknown_model")
    chosen = model_catalog.get(key)
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        book.llm_provider, book.llm_model = chosen.provider, chosen.model
        db.add(ScriptLog(
            book_id=book.id, level="info",
            message=f"V2: модель разметки — {chosen.label} ({chosen.provider}/{chosen.model}), выбрал {actor_name}",
        ))
        record_operator_intervention(
            db, book=book, action_type="v2_set_model", actor_uid=actor_uid, actor_name=actor_name,
            reason="v2: выбор модели разметки",
            payload={"key": chosen.key, "provider": chosen.provider, "model": chosen.model,
                     "trains_on_text": chosen.trains_on_text},
        )
        db.commit()
    return JSONResponse({"ok": True, "model": chosen.as_dict()})


def _takes_with_code(db, code: str) -> int:
    """Сколько принятых файлов подписано этим кодом книги.

    Коды в базе лежат так, как их написал диктор («КП»), а сравниваются
    нормализованными («KP») — поэтому считаем по группам, а не фильтром в SQL.
    """
    rows = db.query(AudioFile.book_code, func.count(AudioFile.id)).group_by(AudioFile.book_code).all()
    return sum(int(count or 0) for value, count in rows if book_token(str(value or "")) == code)


async def api_v2_set_book_title(request: Request, book_id: str):
    """Переименовать книгу: автор и название порознь, витрина собирается сама.

    Смена названия меняет код книги, которым подписаны записи актёров
    (`derive_book_code`), поэтому новый код возвращается ответом — чтобы тот, кто
    переименовал, увидел, что теперь писать в именах файлов.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    title = str(body.get("title") or "").strip()
    author = str(body.get("author") or "").strip()
    if not title:
        return bad_request_response("title_required")
    if len(title) > 200 or len(author) > 120:
        return bad_request_response("too_long")
    actor_uid, actor_name = _actor(request)
    confirm = bool(body.get("confirm"))
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        was = str(book.display_title or book.title or "")
        was_code = book_token(derive_book_code(str(book.title or ""))) or "BOOK"
        display = book_title_service.apply_book_title(book, author=author, title=title)
        code = book_token(derive_book_code(str(book.title or ""))) or "BOOK"
        # У записей нет ссылки на книгу — их держит только код, выведенный из названия
        # (`audio_files.book_code`). Сменить название книги, под которую уже сдают
        # дубли, значит оторвать их от неё; такое делается только с открытыми глазами.
        if code != was_code:
            orphans = _takes_with_code(db, was_code)
            if orphans and not confirm:
                db.rollback()
                return JSONResponse({"ok": False, "error": "code_changes", "was_code": was_code,
                                     "code": code, "files": orphans}, status_code=409)
        if display != was:
            db.add(ScriptLog(
                book_id=book.id, level="info",
                message=f"Книга переименована: «{was}» → «{display}», код для имён файлов — {code}. Переименовал {actor_name}",
            ))
            record_operator_intervention(
                db, book=book, action_type="book_rename", actor_uid=actor_uid, actor_name=actor_name,
                reason="переименование книги",
                payload={"was": was, "now": display, "title": book.title, "author": book.author_label, "code": code},
            )
        db.commit()
        return JSONResponse({"ok": True, "title": book.title, "author": book.author_label,
                             "display_title": display, "book_code": code})


async def api_v2_chapter_approve(request: Request, chapter_id: str):
    """The author's «проверено» on one chapter, or taking it back."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body_or_empty(request)
    if body is None:
        return bad_request_response()
    actor_uid, actor_name = _actor(request)
    approved = bool(body.get("approved", True))
    with SessionLocal() as db:
        try:
            result = set_chapter_approved(
                db, chapter_id=str(chapter_id or ""), approved=approved,
                actor_uid=actor_uid, actor_name=actor_name, notify=_notify_recording(),
            )
        except ValueError:
            return not_found_response("chapter_not_found")
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_approve_all(request: Request, book_id: str):
    """«Прочитал всё»: every marked-up chapter of the book at once."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        result = approve_all(db, book=book, actor_uid=actor_uid, actor_name=actor_name)
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_create_role(request: Request, book_id: str):
    """Add a role to the book's cast — the reader's «новая роль»."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        try:
            result = create_role(
                db, book_id=str(book_id or ""), name=str(body.get("name") or ""),
                actor_uid=actor_uid, actor_name=actor_name,
            )
        except CreateRoleError as exc:
            if exc.code == "book_not_found":
                return not_found_response("book_not_found")
            return bad_request_response(exc.code)
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_auto_publish(request: Request, book_id: str):
    """Whether an approved chapter opens for recording at once."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    enabled = bool(body.get("enabled"))
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        book.auto_publish = "true" if enabled else "false"
        db.add(ScriptLog(
            book_id=book.id, level="info",
            message=("V2: главы открываются дикторам сразу после одобрения" if enabled
                     else "V2: автопубликация одобренных глав выключена") + f" ({actor_name})",
        ))
        record_operator_intervention(
            db, book=book, action_type="v2_auto_publish", actor_uid=actor_uid, actor_name=actor_name,
            reason="v2: автопубликация одобренных глав", payload={"enabled": enabled},
        )
        db.commit()
    return JSONResponse({"ok": True, "auto_publish": enabled})


def api_v2_role_script(request: Request, book_id: str):
    """Every line of one role across the book, with the paragraphs around it."""
    if not _is_authenticated(request):
        return unauthorized_response()
    params = request.query_params
    role = str(params.get("role") or "").strip()
    if not role:
        return bad_request_response("role_required")

    def number(name: str, fallback: int) -> int:
        try:
            return int(params.get(name, fallback))
        except (TypeError, ValueError):
            return fallback

    with SessionLocal() as db:
        payload = role_script(
            db, str(book_id or ""), role,
            radius=number("radius", 2), limit=number("limit", 300), offset=number("offset", 0),
            # Сбор реплик идёт по всей книге сразу — значит и утечь может вся книга.
            published_only=not _can_edit(request),
        )
    if payload is None:
        return not_found_response("book_not_found")
    # актёр ставит ударения прямо здесь — тем же правом, что и в главе
    return JSONResponse({"ok": True, **payload, "can_voice": _can_voice(request)})


def api_v2_role_traps(request: Request, book_id: str):
    """«Слова-ловушки»: редкие слова из реплик роли, по главам, с ударением."""
    if not _is_authenticated(request):
        return unauthorized_response()
    params = request.query_params
    role = str(params.get("role") or "").strip()
    if not role:
        return bad_request_response("role_required")
    fresh = str(params.get("fresh", "1")).strip() not in {"0", "false", "no"}
    with SessionLocal() as db:
        # как и сбор реплик: по всей книге сразу, поэтому диктору — только опубликованное
        payload = role_traps(db, str(book_id or ""), role, fresh=fresh, published_only=not _can_edit(request))
    if payload is None:
        return not_found_response("book_not_found")
    return JSONResponse({"ok": True, **payload})


# --- the pipeline: progress, run, stop, legacy import -----------------------------------


def api_v2_progress(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    with SessionLocal() as db:
        payload = book_progress(db, str(book_id or "").strip())
    if payload is None:
        return not_found_response("book_not_found")
    return JSONResponse({"ok": True, **payload})


def _requested_steps(body: dict) -> tuple[str, ...] | None:
    """The steps the body asks for, in pipeline order; None when a step is unknown."""
    raw = body.get("steps")
    if raw in (None, "", []):
        return STEPS
    if isinstance(raw, str):
        raw = [part for part in raw.replace(",", " ").split() if part]
    if not isinstance(raw, list):
        return None
    chosen = {str(item).strip().lower() for item in raw}
    if not chosen or not chosen <= set(STEPS):
        return None
    return tuple(step for step in STEPS if step in chosen)


async def api_v2_run(request: Request, book_id: str):
    """Put the v2 pipeline on the `high` queue for the book; 409 while a run is active."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body_or_empty(request)
    if body is None:
        return bad_request_response()
    steps = _requested_steps(body)
    if steps is None:
        return bad_request_response("unknown_steps")
    force = bool(body.get("force"))
    actor_uid, actor_name = _actor(request)

    # Смета большой книги читает весь её текст: на цикле событий это морозило бы сайт.
    def prepare():
        with SessionLocal() as db:
            book = db.get(ScriptBook, str(book_id or "").strip())
            if book is None:
                return not_found_response("book_not_found"), None
            expire_stale_runs(db, book.id)
            running = active_run(db, book.id)
            if running is not None:
                db.commit()
                return error_response("run_in_progress", status_code=409, extra={"run_id": running.id, "status": running.status}), None
            estimate, unknown = spend.estimate_markup(db, book, steps, force=force)
            gate = limit_gate(db, request, estimate_rub=estimate, unknown_price=unknown, what="разметка книги")
            if gate is not None:
                db.commit()
                return gate, None
            run = create_queued_run(db, book.id)
            book.pipeline_mode = V2_MODE
            book.status = "processing"
            book.stop_requested = "false"
            db.add(ScriptLog(
                book_id=book.id, level="info",
                message=f"V2: прогон {run.id} поставлен в очередь ({actor_name}): шаги {', '.join(steps)}{' (force)' if force else ''}",
            ))
            db.commit()
            return None, run.id

    early, run_id = await asyncio.to_thread(prepare)
    if early is not None:
        return early

    job_id = enqueue_tracked_task(
        queue_name=V2_QUEUE,
        func_ref=V2_TASK,
        session_factory=SessionLocal,
        background_run_cls=BackgroundRun,
        job_kind="v2_pipeline",
        entity_type="script_book",
        entity_id=book_id,
        run_key=v2_run_key(book_id),
        meta={"book_id": book_id, "v2_run_id": run_id, "steps": list(steps), "force": force, "by": actor_uid},
        book_id=book_id,
        steps=list(steps),
        force=force,
        v2_run_id=run_id,
    )
    if not job_id:
        # Either the queue refused the job or a fresh background run with the same key
        # is already there (the launcher's own dedup); the row must not stay «queued».
        with SessionLocal() as db:
            run = db.get(V2Run, run_id)
            if run is not None:
                run.status = "failed"
                run.error = "enqueue failed: the queue refused the job or one is already running"
                db.commit()
        return error_response("enqueue_failed", status_code=409, extra={"run_id": run_id})
    return JSONResponse(with_warning(request, {"ok": True, "run_id": run_id, "job_id": job_id, "steps": list(steps),
                                               "force": force}))


async def api_v2_stop(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    _, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        book.stop_requested = "true"
        running = active_run(db, book.id)
        db.add(ScriptLog(book_id=book.id, level="info", message=f"V2: запрошена остановка ({actor_name})"))
        db.commit()
        return JSONResponse({"ok": True, "stop_requested": True, "run_id": running.id if running else None})


async def api_v2_character_recast(request: Request, character_id: str):
    """`POST /api/v2/characters/{id}/recast` — заменить актёра персонажа по циклу «вперёд»."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    from app.auth import session_roles
    from app.models import Character
    from app.services.casting import rebuild_assignments, recast
    from app.services.role_votes import vote_weight

    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        character = db.get(Character, str(character_id or "").strip())
        if character is None:
            return not_found_response("character_not_found")
        try:
            result = recast(db, character, to_actor=str(body.get("to_actor") or ""),
                            reason=str(body.get("reason") or ""), comment=str(body.get("comment") or ""),
                            voter_uid=actor_uid, voter_name=actor_name, weight=vote_weight(session_roles(request)))
        except ValueError as exc:
            return bad_request_response(str(exc))
        for book_id in {str(character.book_id), *[row["book_id"] for row in result["changed"]]}:
            rebuild_assignments(db, book_id)
        db.commit()
        if result["changed"]:
            from app.services.role_approval import notify_role_approved

            first, *rest = result["changed"]
            result["approval"] = notify_role_approved(
                db, book_id=first["book_id"], book_title=first["book_title"], role=str(character.name or ""),
                actor_name=" ".join(str(body.get("to_actor") or "").split()),
                also_in=[row["book_title"] for row in rest],
            )
            db.commit()
    return JSONResponse({"ok": True, **result})


def api_v2_character_recast_preview(request: Request, character_id: str):
    """`GET /api/v2/characters/{id}/recast-preview?to_actor=` — где сменится, где останется."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.models import Character
    from app.services.casting import recast_preview

    with SessionLocal() as db:
        character = db.get(Character, str(character_id or "").strip())
        if character is None:
            return not_found_response("character_not_found")
        try:
            preview = recast_preview(db, character, to_actor=str(request.query_params.get("to_actor") or ""))
        except ValueError as exc:
            return bad_request_response(str(exc))
    return JSONResponse({"ok": True, **preview})


def api_v2_cast_discrepancies(request: Request, author_id: str):
    """`GET /api/v2/authors/{id}/cast-discrepancies` — где у персонажа цикла разные актёры."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response("read_only")
    from app.services.casting import cycle_discrepancies

    with SessionLocal() as db:
        return JSONResponse({"ok": True, "items": cycle_discrepancies(db, str(author_id or "").strip())})


async def api_v2_reassign_segment(request: Request, segment_id: str):
    """`POST /api/v2/segments/{segment_id}/attribution` — the operator's word on who speaks."""
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body(request)
    if body is None:
        return bad_request_response()
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        try:
            impacts = impacts_for_reassign(db, segment_id=segment_id, spans=body.get("spans") or [])
        except Exception:
            # Предупреждение о звуке — подсказка, а не условие правки: его сбой не должен
            # отбирать у человека саму правку разметки.
            logger.exception("Не удалось посчитать предупреждение о звуке, сегмент %s", segment_id)
            db.rollback()
            impacts = []
        try:
            result = reassign_segment(
                db, segment_id=segment_id, spans=body.get("spans"),
                actor_uid=actor_uid, actor_name=actor_name,
            )
        except ReassignError as exc:
            if exc.code == "segment_not_found":
                return not_found_response(exc.code)
            return bad_request_response(exc.code)
        db.commit()
        chapter_id = chapter_of_segment(db, segment_id)
        recorded = bool(chapter_id) and chapter_has_recognition(db, chapter_id)
    if recorded:
        _queue_realign(chapter_id)
    return JSONResponse({"ok": True, **result, "recording_impact": impacts})


def _notify_recording():
    from app.services.telegram import notify_book_ready_for_recording

    return notify_book_ready_for_recording


async def api_v2_publish(request: Request, book_id: str):
    """`POST /api/v2/books/{book_id}/publish` — approved chapters to the actors.

    `silent: true` publishes without messaging the dictors, for when the owner will
    tell them himself.
    """
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    body = await _json_body_or_empty(request)
    if body is None:
        return bad_request_response()
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        silent = bool((body or {}).get("silent"))
        result = publish_book(
            db, book=book, actor_uid=actor_uid, actor_name=actor_name,
            notify=None if silent else _notify_recording(),
        )
        db.commit()
    return JSONResponse({"ok": True, **result})


async def api_v2_unpublish(request: Request, book_id: str):
    if not _is_authenticated(request):
        return unauthorized_response()
    if not _can_edit(request):
        return forbidden_response()
    actor_uid, actor_name = _actor(request)
    with SessionLocal() as db:
        book = db.get(ScriptBook, str(book_id or "").strip())
        if book is None:
            return not_found_response("book_not_found")
        result = unpublish_book(db, book=book, actor_uid=actor_uid, actor_name=actor_name)
        db.commit()
    return JSONResponse({"ok": True, **result})
