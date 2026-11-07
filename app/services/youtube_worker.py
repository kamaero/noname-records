"""Фоновая загрузка с сохранением аудио и сведений для кредитов."""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from app.models import (SoundtrackEntry, SoundtrackState, SoundtrackWork,
                        YoutubeSoundtrackDownload)
from app.services.soundtrack_store import _scene


def perform(session_factory, download_id: str):
    from app.services.youtube_download import download_youtube_audio, YoutubeDownloadError
    from app.services.audio_mirror import LocalDiskFull

    with session_factory() as db:
        claimed = db.execute(update(YoutubeSoundtrackDownload).where(
            YoutubeSoundtrackDownload.id == download_id,
            YoutubeSoundtrackDownload.status == "queued",
        ).values(status="running")).rowcount
        db.commit()
        if not claimed:
            return

    result = None
    try:
        with session_factory() as db:
            from app.models import ScriptChapter
            job = db.get(YoutubeSoundtrackDownload, download_id)
            chapter = db.get(ScriptChapter, job.chapter_id) if job else None
            if job is None or chapter is None or chapter.book_id != job.book_id:
                raise ValueError("chapter_unavailable")
            result = download_youtube_audio(db, video_id=job.video_id, chapter_id=job.chapter_id,
                                            rights_confirmed=True, task_id=job.id)
            scene_id, scene_title = _scene(db, chapter, job.scene_id)
            db.execute(insert(SoundtrackState).values(chapter_id=chapter.id, book_id=chapter.book_id,
                       version=0, locked=False).on_conflict_do_nothing(index_elements=["chapter_id"]))
            changed = db.execute(update(SoundtrackState).where(SoundtrackState.chapter_id == chapter.id,
                SoundtrackState.locked.is_(False)).values(version=SoundtrackState.version + 1)).rowcount
            if not changed:
                raise ValueError("soundtrack_locked")
            meta = result.provenance
            original = {
                "title": str(meta.get("title") or "YouTube")[:300],
                "author": str(meta.get("uploader") or "")[:300],
                "source_url": str(meta.get("source_url") or "")[:2048],
                "license": str(meta.get("declared_license") or "")[:100],
                "attribution": job.rights_note[:10000],
                "rights_status": "unknown",
                "rights_warning": "Rights were confirmed by the user, not verified automatically.",
                "rights_confirmation": job.rights_note,
                "confirmed_by": job.confirmed_by,
                "audio_file_id": result.audio_file.id,
                "duration_seconds": result.audio_file.duration_seconds,
            }
            db.execute(insert(SoundtrackWork).values(book_id=job.book_id, source="youtube",
                       identity=job.video_id, original=original).on_conflict_do_nothing(
                       index_elements=["book_id", "source", "identity"]))
            work = db.scalar(select(SoundtrackWork).where(
                SoundtrackWork.book_id == job.book_id, SoundtrackWork.source == "youtube",
                SoundtrackWork.identity == job.video_id))
            entry = db.scalar(select(SoundtrackEntry).where(
                SoundtrackEntry.chapter_id == chapter.id, SoundtrackEntry.work_id == work.id,
                SoundtrackEntry.scene_id == scene_id, SoundtrackEntry.layer == job.layer))
            if entry is None:
                entry = SoundtrackEntry(book_id=job.book_id, chapter_id=chapter.id, work_id=work.id,
                                        scene_id=scene_id, scene_title=scene_title, layer=job.layer, used=False,
                                        overrides={"title": original["title"], "author": original["author"],
                                                   "source_url": original["source_url"], "license": original["license"],
                                                   "attribution": original["attribution"]})
                db.add(entry)
            # Один ролик общий для книги, но файл и подтверждение принадлежат
            # конкретной загрузке: другая глава не должна получить чужие права.
            entry.overrides = {**entry.overrides,
                               "audio_file_id": result.audio_file.id,
                               "duration_seconds": result.audio_file.duration_seconds,
                               "rights_confirmation": job.rights_note,
                               "confirmed_by": job.confirmed_by}
            entry.removed = False
            job.audio_file_id = result.audio_file.id
            job.metadata_json = {**meta, "rights_note": job.rights_note, "confirmed_by": job.confirmed_by}
            job.status = "done"
            db.commit()
    except Exception as exc:
        if result is not None:
            result.cleanup()
        code = ("youtube_local_disk_full" if isinstance(exc, LocalDiskFull)
                else str(exc) if isinstance(exc, YoutubeDownloadError) else "youtube_download_failed")
        with session_factory() as db:
            job = db.get(YoutubeSoundtrackDownload, download_id)
            if job is not None and job.status == "running":
                job.status, job.error_code = "failed", str(code)[:80]
                db.commit()
    else:
        # Ускоряем штатный цикл только после commit: второй копировщик не нужен.
        from app.services.mirror_wakeup import request_mirror_wakeup
        request_mirror_wakeup()
