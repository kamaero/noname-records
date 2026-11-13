"""Заголовки остаются исходным текстом, но читаются отдельно по политике книги."""
from __future__ import annotations

from types import SimpleNamespace

TITLE_ROLE = "Названия глав"


def title_actor(book) -> str:
    return str(getattr(book, "chapter_title_actor_name", "") or "").strip()


def title_enabled(db, book_id: str) -> bool:
    from app.models import ScriptBook

    return bool(title_actor(db.get(ScriptBook, str(book_id))))


def recording_rows(db, segments, rows_by_segment) -> dict:
    """Не меняем атрибуцию: служебная роль существует только в проекции записи."""
    enabled = {book_id: title_enabled(db, book_id) for book_id in {str(segment.book_id) for segment in segments}}
    result = {}
    for segment in segments:
        if segment.kind == "heading" and enabled.get(str(segment.book_id)):
            result[segment.id] = [SimpleNamespace(
                id=f"title:{segment.id}", segment_id=segment.id, speaker=TITLE_ROLE,
                span_start=0, span_end=len(str(segment.text or "")), source="chapter_title",
                version=1, confidence=1.0,
            )] if str(segment.text or "").strip() else []
        else:
            result[segment.id] = [SimpleNamespace(
                **{key: getattr(row, key, None) for key in (
                    "id", "segment_id", "speaker", "span_start", "span_end", "source", "version", "confidence", "role_ref")}
            ) for row in rows_by_segment.get(segment.id, [])]
    return result


def chapter_targets(db, book_id: str) -> list[dict]:
    """Одна запись названий покрывает и ещё не опубликованные главы книги."""
    from app.models import ScriptChapter
    from app.v2.models import V2Segment

    if not title_enabled(db, book_id):
        return []
    segments = db.query(V2Segment).filter(
        V2Segment.book_id == str(book_id), V2Segment.kind == "heading"
    ).order_by(V2Segment.ordinal.asc()).all()
    by_chapter = {}
    for segment in segments:
        if str(segment.text or "").strip():
            by_chapter.setdefault(str(segment.chapter_id), []).append(segment)
    return [{"chapter_id": str(chapter.id), "chapter_index": int(chapter.chapter_index or 0),
             "chapter_title": str(chapter.chapter_title or ""),
             "lines": [str(segment.text).strip() for segment in by_chapter[str(chapter.id)]],
             "segment_ids": [str(segment.id) for segment in by_chapter[str(chapter.id)]]}
            for chapter in db.query(ScriptChapter).filter(ScriptChapter.book_id == str(book_id))
            .order_by(ScriptChapter.chapter_index.asc()).all() if str(chapter.id) in by_chapter]


def configure(db, book_id: str, actor: str, *, apply: bool = False, commit_each: bool = False) -> dict:
    """Пересчёт использует сохранённое услышанное и не рассылает повторные письма."""
    from app.models import AsrJob, AudioFile, ScriptBook, ScriptChapter
    from app.services.asr_replay import realign_job
    from app.services.chapter_delivery import mark_session_outdated
    from app.services.shared_runtime import is_narrator_name
    from app.services.author_profile import normalize_name

    book = db.get(ScriptBook, str(book_id))
    if book is None:
        raise ValueError("book_not_found")
    actor = str(actor or "").strip()
    if not actor or len(actor) > 120:
        raise ValueError("chapter_title_actor_required")
    before = title_actor(book)
    if before and normalize_name(before) != normalize_name(actor) and db.query(AudioFile.id).filter(
        AudioFile.role_book_id == str(book.id), AudioFile.role == TITLE_ROLE, AudioFile.kind == "take"
    ).first():
        raise ValueError("chapter_title_actor_has_recordings")
    reports, unsafe = [], []
    # Холостой просмотр должен видеть новую политику в памяти, но не сбрасывать
    # её в базу даже временно через автоматический flush очередного SELECT.
    try:
        with db.no_autoflush:
            book.chapter_title_actor_name = actor
            chapter_ids = [row.id for row in db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id)]
            if apply:
                # Если старый heard_json повреждён, уже опубликованная политика
                # всё равно не должна оставлять архив с прежними индексами свежим.
                for chapter_id in chapter_ids:
                    mark_session_outdated(db, chapter_id)
            if apply and commit_each:
                db.commit()
            jobs = db.query(AsrJob).filter(AsrJob.chapter_id.in_(chapter_ids), AsrJob.status == "done").all()
            for job in jobs:
                from app.services.role_identity import chapter_roles
                if str(job.expected_role or "") != TITLE_ROLE and not chapter_roles(db, job.chapter_id).is_narrator(str(job.expected_role or ""), str(job.role_ref or "")):
                    continue
                report = realign_job(db, job.id, write=apply)
                if report is None:
                    unsafe.append(str(job.id))
                else:
                    reports.append(report)
                if apply and commit_each:
                    db.commit()
    finally:
        if not apply:
            book.chapter_title_actor_name = before
    if apply:
        db.flush()
    return {"book_id": str(book.id), "actor": actor, "previous_actor": before,
            "apply": apply, "jobs": reports, "unsafe_job_ids": unsafe,
            "chapters": len(chapter_ids)}
