"""Поиск YouTube и ручное подтверждение загрузки для сцены."""
from __future__ import annotations

import asyncio
import re

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.auth import session_payload
from app.db import SessionLocal
from app.models import AudioFile, SoundMarker, SoundtrackState, YoutubeSoundtrackDownload
from app.services.youtube_client import YouTubeError, client, video_id
from app.v2.api import _can_edit, _is_authenticated, _json_body_or_empty
from app.services.soundtrack_store import chapter_for, SoundtrackError


def _error(code: str, status: int = 400):
    return JSONResponse({"ok": False, "error": code}, status_code=status)


def _access(request):
    if not _is_authenticated(request):
        return _error("unauthorized", 401)
    if not _can_edit(request):
        return _error("read_only", 403)
    return None


def search(request: Request, chapter_id: str):
    denied = _access(request)
    if denied is not None:
        return denied
    with SessionLocal() as db:
        try:
            chapter_for(db, chapter_id)
        except SoundtrackError:
            return _error("not_found", 404)
    try:
        return JSONResponse({"ok": True, **client.search(request.query_params.get("q", ""))})
    except YouTubeError as exc:
        return _error(exc.code, exc.status)
    except ValueError:
        return _error("bad_youtube_search")


def detail(request: Request, chapter_id: str, video: str):
    denied = _access(request)
    if denied is not None:
        return denied
    with SessionLocal() as db:
        try:
            chapter_for(db, chapter_id)
        except SoundtrackError:
            return _error("not_found", 404)
    try:
        return JSONResponse({"ok": True, "result": client.detail(video)})
    except YouTubeError as exc:
        return _error(exc.code, exc.status)
    except ValueError:
        return _error("bad_video")


def _payload(db, job):
    from app.config import settings
    audio = db.get(AudioFile, job.audio_file_id) if job.audio_file_id else None
    mirror = audio.mirror_state if audio else None
    if audio and not settings.audio_nas_path and not mirror:
        mirror = "disabled"
    return {"ok": True, "job_id": job.id, "status": job.status, "error": job.error_code or None,
            "audio_file_id": job.audio_file_id, "mirror_state": mirror,
            "scene_id": job.scene_id, "layer": job.layer, "video_id": job.video_id,
            "title": str((job.metadata_json or {}).get("title") or f"YouTube {job.video_id}")}


def _settle_lost(db, job):
    """Упавший RQ-процесс не должен бесконечно закрывать закрепление главы."""
    from datetime import timedelta
    from sqlalchemy import update
    from app.time_utils import utcnow_naive
    from app.workers.launcher import get_queue
    if job.status not in ("queued", "running"):
        return
    from app.seat import one_seat
    stale = (job.updated_at or job.created_at) < utcnow_naive() - timedelta(hours=3)
    if one_seat():
        # У потока нет RQ-записи. Только начатая загрузка имеет жёсткий предел
        # 45 + 300 секунд; queued может ждать долгий прогон в общей линии.
        lost = job.status == "running" and stale
    else:
        try:
            rq_job = get_queue("default").fetch_job(f"youtube-{job.id}")
            rq_status = rq_job.get_status(refresh=True) if rq_job else None
        except Exception:
            # Недоступный Redis не доказывает, что задача погибла.
            return
        lost = rq_status in ("failed", "stopped", "canceled") or (rq_job is None and stale) or (rq_status == "finished" and stale)
    if lost:
        db.execute(update(YoutubeSoundtrackDownload).where(
            YoutubeSoundtrackDownload.id == job.id,
            YoutubeSoundtrackDownload.status == job.status,
        ).values(status="failed", error_code="youtube_worker_interrupted"))
        db.commit()
        db.refresh(job)


def status(request: Request, chapter_id: str, job_id: str):
    denied = _access(request)
    if denied is not None:
        return denied
    with SessionLocal() as db:
        job = db.get(YoutubeSoundtrackDownload, job_id)
        if job is None or job.chapter_id != chapter_id:
            return _error("not_found", 404)
        _settle_lost(db, job)
        return JSONResponse(_payload(db, job))


def jobs(request: Request, chapter_id: str):
    denied = _access(request)
    if denied is not None:
        return denied
    with SessionLocal() as db:
        try:
            chapter_for(db, chapter_id)
        except SoundtrackError:
            return _error("not_found", 404)
        rows = (db.query(YoutubeSoundtrackDownload).filter_by(chapter_id=chapter_id)
                .order_by(YoutubeSoundtrackDownload.created_at.desc()).limit(100).all())
        for job in rows:
            _settle_lost(db, job)
        return JSONResponse({"ok": True, "jobs": [_payload(db, job) for job in rows]})


def _enqueue(job_id):
    from app.seat import one_seat
    from app.workers.launcher import get_queue
    queue = get_queue("default")
    if one_seat():
        # Локальная очередь передаёт keyword в задачу и не знает RQ job_id.
        # Повтор безопасен: воркер атомарно забирает queued-строку.
        queue.enqueue("app.worker_tasks.perform_youtube_soundtrack_task", download_id=job_id,
                      job_timeout=7200, result_ttl=86400)
        return
    rq_id = f"youtube-{job_id}"
    if queue.fetch_job(rq_id) is None:
        queue.enqueue("app.worker_tasks.perform_youtube_soundtrack_task", download_id=job_id,
                      job_id=rq_id, job_timeout=7200, result_ttl=86400)


def _create(chapter_id, body, actor):
    if body.get("rights_confirmed") is not True:
        return _error("rights_confirmation_required")
    rid, sid, layer, note = (body.get("request_id"), body.get("scene_id"),
                             body.get("layer"), body.get("license_note"))
    if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rid):
        return _error("bad_request_id")
    if not isinstance(sid, str) or len(sid) > 36 or layer not in ("music", "ambience", "fx"):
        return _error("bad_scene")
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 1000:
        return _error("license_note_required")
    try:
        vid = video_id(body.get("video_id"))
    except (ValueError, YouTubeError, TypeError):
        return _error("bad_video")
    with SessionLocal() as db:
        # Очередь и закрепление главы не должны пройти одновременно.
        db.execute(text("BEGIN IMMEDIATE"))
        try:
            chapter = chapter_for(db, chapter_id)
        except SoundtrackError:
            return _error("not_found", 404)
        marker = db.get(SoundMarker, sid)
        if marker is None or marker.chapter_id != chapter.id or marker.book_id != chapter.book_id or marker.kind != "scene" or marker.status != "active":
            return _error("not_found", 404)
        state = db.get(SoundtrackState, chapter.id)
        if state and state.locked:
            return _error("soundtrack_locked", 409)
        old = db.query(YoutubeSoundtrackDownload).filter_by(chapter_id=chapter_id, request_id=rid).first()
        if old:
            if (old.video_id, old.scene_id, old.layer, old.rights_note, old.confirmed_by) != (vid, sid, layer, note.strip(), actor):
                return _error("request_conflict", 409)
            old_id, old_status = old.id, old.status
            payload = _payload(db, old)
            db.rollback()
            if old_status == "queued":
                try:
                    _enqueue(old_id)
                except Exception:
                    return _error("queue_unavailable", 503)
            return JSONResponse(payload)
        job = YoutubeSoundtrackDownload(book_id=chapter.book_id, chapter_id=chapter.id, scene_id=sid,
                                        layer=layer, video_id=vid, request_id=rid, rights_note=note.strip(),
                                        confirmed_by=actor, status="queued")
        db.add(job)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            old = db.query(YoutubeSoundtrackDownload).filter_by(chapter_id=chapter_id, request_id=rid).first()
            if old and (old.video_id, old.scene_id, old.layer, old.rights_note, old.confirmed_by) == (vid, sid, layer, note.strip(), actor):
                return JSONResponse(_payload(db, old))
            return _error("request_conflict", 409)
        job_id = job.id
    try:
        _enqueue(job_id)
    except Exception:
        with SessionLocal() as db:
            job = db.get(YoutubeSoundtrackDownload, job_id)
            if job and job.status == "queued":
                job.status, job.error_code = "failed", "queue_unavailable"
                db.commit()
        return _error("queue_unavailable", 503)
    return JSONResponse({"ok": True, "job_id": job_id, "status": "queued"})


async def download(request: Request, chapter_id: str):
    denied = _access(request)
    if denied is not None:
        return denied
    body = await _json_body_or_empty(request)
    if not isinstance(body, dict):
        return _error("bad_request")
    actor = str(session_payload(request).get("uid") or "")
    if not actor:
        return _error("unauthorized", 401)
    return await asyncio.to_thread(_create, chapter_id, body, actor)
