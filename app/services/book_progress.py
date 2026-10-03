from datetime import datetime

from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.constants import CHAPTER_READY_FOR_REVIEW, status_label
from app.models import ScriptChapter, ScriptJob
from app.time_utils import utcnow_naive


def _build_stage_status_counts(book_ids: list[str]) -> dict[str, dict[str, dict[str, int]]]:
    return {
        book_id: {"char_extraction": {}, "draft": {}, "final": {}, "unsure_pass": {}, "polishing": {}}
        for book_id in book_ids
    }


def _load_status_counts(db, book_ids: list[str]) -> dict[str, dict[str, int]]:
    status_counts: dict[str, dict[str, int]] = {book_id: {} for book_id in book_ids}
    try:
        rows = (
            db.query(ScriptJob.book_id, ScriptJob.status, func.count(ScriptJob.id))
            .filter(ScriptJob.book_id.in_(book_ids))
            .group_by(ScriptJob.book_id, ScriptJob.status)
            .all()
        )
        for row in rows:
            status_counts[row[0]][row[1]] = int(row[2])
    except SQLAlchemyError:
        for book_id in book_ids:
            for row in db.query(ScriptJob.status).filter(ScriptJob.book_id == book_id).all():
                status = str(row[0] or "")
                if not status:
                    continue
                status_counts[book_id][status] = status_counts[book_id].get(status, 0) + 1
    return status_counts


def _load_stage_status_counts(db, book_ids: list[str]) -> dict[str, dict[str, dict[str, int]]]:
    stage_status_counts = _build_stage_status_counts(book_ids)
    try:
        rows = (
            db.query(ScriptJob.book_id, ScriptJob.stage, ScriptJob.status, func.count(ScriptJob.id))
            .filter(ScriptJob.book_id.in_(book_ids))
            .group_by(ScriptJob.book_id, ScriptJob.stage, ScriptJob.status)
            .all()
        )
        for row in rows:
            bucket = stage_status_counts.setdefault(row[0], {}).setdefault(str(row[1]), {})
            bucket[str(row[2])] = int(row[3])
    except SQLAlchemyError:
        for book_id in book_ids:
            for row in db.query(ScriptJob.stage, ScriptJob.status).filter(ScriptJob.book_id == book_id).all():
                stage = str(row[0] or "")
                status = str(row[1] or "")
                if not stage or not status:
                    continue
                bucket = stage_status_counts.setdefault(book_id, {}).setdefault(stage, {})
                bucket[status] = bucket.get(status, 0) + 1
    return stage_status_counts


def _load_done_chapters(db, book_ids: list[str]) -> dict[str, int]:
    done_chapters: dict[str, int] = {}
    try:
        return {
            row[0]: int(row[1])
            for row in (
                db.query(ScriptChapter.book_id, func.count(ScriptChapter.id))
                .filter(ScriptChapter.book_id.in_(book_ids), ScriptChapter.status.in_(CHAPTER_READY_FOR_REVIEW))
                .group_by(ScriptChapter.book_id)
                .all()
            )
        }
    except SQLAlchemyError:
        for book_id in book_ids:
            done = 0
            try:
                for row in db.query(ScriptChapter.status).filter(ScriptChapter.book_id == book_id).all():
                    if str(row[0] or "") in CHAPTER_READY_FOR_REVIEW:
                        done += 1
            except SQLAlchemyError:
                done = 0
            done_chapters[book_id] = done
    return done_chapters


def _load_processing_jobs(db, book_ids: list[str]) -> dict[str, list[ScriptJob]]:
    processing_jobs_by_book: dict[str, list[ScriptJob]] = {book_id: [] for book_id in book_ids}
    try:
        rows = (
            db.query(ScriptJob)
            .filter(ScriptJob.book_id.in_(book_ids), ScriptJob.status == "processing")
            .order_by(ScriptJob.book_id.asc(), ScriptJob.updated_at.desc())
            .all()
        )
    except SQLAlchemyError:
        rows = []
        for book_id in book_ids:
            rows.extend(
                db.query(ScriptJob)
                .filter(ScriptJob.book_id == book_id, ScriptJob.status == "processing")
                .order_by(ScriptJob.updated_at.desc())
                .all()
            )
    for row in rows:
        processing_jobs_by_book.setdefault(row.book_id, []).append(row)
    return processing_jobs_by_book


def _load_stage_timings(db, book_ids: list[str]) -> dict[str, dict[str, dict[str, datetime | None]]]:
    stage_timings: dict[str, dict[str, dict[str, datetime | None]]] = {
        book_id: {
            "char_extraction": {"min_created": None, "max_updated": None},
            "draft": {"min_created": None, "max_updated": None},
            "final": {"min_created": None, "max_updated": None},
            "unsure_pass": {"min_created": None, "max_updated": None},
            "polishing": {"min_created": None, "max_updated": None},
        }
        for book_id in book_ids
    }
    try:
        timing_rows = (
            db.query(ScriptJob.book_id, ScriptJob.stage, ScriptJob.created_at, ScriptJob.updated_at)
            .filter(ScriptJob.book_id.in_(book_ids))
            .all()
        )
    except SQLAlchemyError:
        timing_rows = []
        for book_id in book_ids:
            timing_rows.extend(
                db.query(ScriptJob.book_id, ScriptJob.stage, ScriptJob.created_at, ScriptJob.updated_at)
                .filter(ScriptJob.book_id == book_id)
                .all()
            )
    for row in timing_rows:
        book_id = str(row[0] or "")
        stage = str(row[1] or "")
        if book_id not in stage_timings or stage not in stage_timings[book_id]:
            continue
        bucket = stage_timings[book_id][stage]
        created_at = row[2]
        updated_at = row[3]
        if created_at and (bucket["min_created"] is None or created_at < bucket["min_created"]):
            bucket["min_created"] = created_at
        if updated_at and (bucket["max_updated"] is None or updated_at > bucket["max_updated"]):
            bucket["max_updated"] = updated_at
    return stage_timings


def derive_book_pipeline_status(book, progress: dict | None = None) -> str:
    data = progress or {}
    queued = int(data.get("queued") or 0)
    waiting = int(data.get("waiting") or 0)
    processing = int(data.get("processing") or 0)
    overdue_processing = int(data.get("overdue_processing") or 0)
    failed = int(data.get("failed") or 0)
    blocked = int(data.get("blocked") or 0)
    char_extraction_processing = int(data.get("char_extraction_processing") or 0)
    char_extraction_queued = int(data.get("char_extraction_queued") or 0)
    chapters_done = int(data.get("chapters_done") or 0)
    chapter_total = int(data.get("chapters_total") or getattr(book, "chapter_count", 0) or 0)
    current_status = str(getattr(book, "status", "") or "").strip()
    review_statuses = {"author_review", "partially_approved", "approved", "published_to_dictor"}

    if str(getattr(book, "stop_requested", "") or "").lower() == "true":
        return "stopped" if current_status in {"queued", "processing", "stalled", "stopping"} else (current_status or "stopped")
    if current_status == "char_extracting" and (char_extraction_processing > 0 or char_extraction_queued > 0):
        return "stalled" if overdue_processing > 0 else "char_extracting"
    if processing > 0 and overdue_processing >= processing:
        return "stalled"
    if queued > 0 or waiting > 0 or processing > 0:
        return "processing"
    if failed > 0 or blocked > 0:
        return "failed"
    if current_status in {"partially_approved", "approved", "published_to_dictor"}:
        return current_status
    if chapters_done > 0 or current_status in review_statuses:
        return "author_review"
    if chapter_total <= 0:
        return current_status or "uploaded"
    return current_status or "uploaded"


def collect_books_progress(db, books: list) -> dict[str, dict]:
    if not books:
        return {}

    book_ids = [book.id for book in books]
    status_counts = _load_status_counts(db, book_ids)
    stage_status_counts = _load_stage_status_counts(db, book_ids)
    done_chapters = _load_done_chapters(db, book_ids)
    processing_jobs_by_book = _load_processing_jobs(db, book_ids)
    stage_timings = _load_stage_timings(db, book_ids)

    out: dict[str, dict] = {}
    now = utcnow_naive()
    for book in books:
        counts = status_counts.get(book.id, {})
        per_stage = stage_status_counts.get(book.id, {})
        char_extraction_counts = per_stage.get("char_extraction", {})
        draft_counts = per_stage.get("draft", {})
        final_counts = per_stage.get("final", {})
        unsure_counts = per_stage.get("unsure_pass", {})
        polishing_counts = per_stage.get("polishing", {})
        chapters_done = done_chapters.get(book.id, 0)
        total_chapters = max(1, book.chapter_count or 0)
        char_extraction_total = sum(int(v or 0) for v in char_extraction_counts.values())
        polishing_total = sum(int(v or 0) for v in polishing_counts.values())
        total_job_units = max(1, char_extraction_total + total_chapters * 3 + polishing_total)
        terminal_char_extraction_units = char_extraction_counts.get("done", 0) + char_extraction_counts.get("skipped", 0)
        terminal_polishing_units = polishing_counts.get("done", 0) + polishing_counts.get("skipped", 0)
        terminal_unsure_units = unsure_counts.get("done", 0) + unsure_counts.get("skipped", 0)
        done_job_units = (
            terminal_char_extraction_units
            + draft_counts.get("done", 0)
            + final_counts.get("done", 0)
            + terminal_unsure_units
            + terminal_polishing_units
        )
        active_job_units = (
            char_extraction_counts.get("processing", 0)
            + draft_counts.get("processing", 0)
            + final_counts.get("processing", 0)
            + unsure_counts.get("processing", 0)
            + polishing_counts.get("processing", 0)
        )
        percent = int(((done_job_units + (active_job_units * 0.5)) / total_job_units) * 100)
        timeout_seconds = max(30, int(settings.llm_request_timeout_seconds or 180))
        retries = max(1, int(settings.llm_transport_retries or 1))
        stale_seconds = max(300, int(settings.llm_processing_stale_minutes or 35) * 60)
        overdue_after_seconds = max(stale_seconds, timeout_seconds * retries + 30)
        processing_jobs = processing_jobs_by_book.get(book.id, [])
        overdue_processing = 0
        current_job = processing_jobs[0] if processing_jobs else None
        current_job_age_seconds = 0
        if current_job and current_job.updated_at:
            current_job_age_seconds = max(0, int((now - current_job.updated_at).total_seconds()))
        for job in processing_jobs:
            if not job.updated_at:
                continue
            age_seconds = max(0, int((now - job.updated_at).total_seconds()))
            if age_seconds >= overdue_after_seconds:
                overdue_processing += 1
        pipeline_started_at = getattr(book, "created_at", None)
        pipeline_last_updated_at = None
        for stage_name in ("char_extraction", "draft", "final", "unsure_pass", "polishing"):
            stage_updated = stage_timings.get(book.id, {}).get(stage_name, {}).get("max_updated")
            if stage_updated and (pipeline_last_updated_at is None or stage_updated > pipeline_last_updated_at):
                pipeline_last_updated_at = stage_updated
        if not pipeline_last_updated_at and current_job and current_job.updated_at:
            pipeline_last_updated_at = current_job.updated_at
        pipeline_elapsed_seconds = 0
        if pipeline_started_at:
            pipeline_elapsed_seconds = max(
                0,
                int(((pipeline_last_updated_at or now) - pipeline_started_at).total_seconds()),
            )
        stage_elapsed: dict[str, int] = {}
        for stage_name in ("char_extraction", "draft", "final", "unsure_pass", "polishing"):
            stage_info = stage_timings.get(book.id, {}).get(stage_name, {})
            stage_started_at = stage_info.get("min_created")
            stage_finished_at = stage_info.get("max_updated")
            if stage_started_at:
                stage_elapsed[stage_name] = max(0, int(((stage_finished_at or now) - stage_started_at).total_seconds()))
            else:
                stage_elapsed[stage_name] = 0
        out[book.id] = {
            "chapters_done": chapters_done,
            "chapters_total": book.chapter_count,
            "percent": max(0, min(percent, 100)),
            "job_units_done": done_job_units,
            "job_units_total": total_job_units,
            "queued": counts.get("queued", 0),
            "processing": counts.get("processing", 0),
            "waiting": counts.get("waiting", 0),
            "failed": counts.get("failed", 0),
            "blocked": counts.get("blocked", 0),
            "done": counts.get("done", 0),
            "char_extraction_done": char_extraction_counts.get("done", 0),
            "char_extraction_processing": char_extraction_counts.get("processing", 0),
            "char_extraction_queued": char_extraction_counts.get("queued", 0),
            "char_extraction_failed": char_extraction_counts.get("failed", 0),
            "draft_done": draft_counts.get("done", 0),
            "draft_processing": draft_counts.get("processing", 0),
            "draft_queued": draft_counts.get("queued", 0),
            "draft_failed": draft_counts.get("failed", 0),
            "final_done": final_counts.get("done", 0),
            "final_processing": final_counts.get("processing", 0),
            "final_queued": final_counts.get("queued", 0),
            "final_waiting": final_counts.get("waiting", 0),
            "final_failed": final_counts.get("failed", 0),
            "unsure_pass_done": unsure_counts.get("done", 0),
            "unsure_pass_skipped": unsure_counts.get("skipped", 0),
            "unsure_pass_processing": unsure_counts.get("processing", 0),
            "unsure_pass_queued": unsure_counts.get("queued", 0),
            "unsure_pass_waiting": unsure_counts.get("waiting", 0),
            "unsure_pass_failed": unsure_counts.get("failed", 0),
            "polishing_done": polishing_counts.get("done", 0),
            "polishing_processing": polishing_counts.get("processing", 0),
            "polishing_queued": polishing_counts.get("queued", 0),
            "polishing_waiting": polishing_counts.get("waiting", 0),
            "polishing_failed": polishing_counts.get("failed", 0),
            "polishing_skipped": polishing_counts.get("skipped", 0),
            "overdue_processing": overdue_processing,
            "processing_overdue_after_seconds": overdue_after_seconds,
            "current_job_age_seconds": current_job_age_seconds,
            "current_job_is_overdue": bool(current_job and current_job_age_seconds >= overdue_after_seconds),
            "pipeline_elapsed_seconds": pipeline_elapsed_seconds,
            "char_extraction_elapsed_seconds": stage_elapsed["char_extraction"],
            "draft_elapsed_seconds": stage_elapsed["draft"],
            "final_elapsed_seconds": stage_elapsed["final"],
            "unsure_pass_elapsed_seconds": stage_elapsed["unsure_pass"],
            "polishing_elapsed_seconds": stage_elapsed["polishing"],
        }
        out[book.id]["effective_status"] = derive_book_pipeline_status(book, out[book.id])
        out[book.id]["effective_status_label"] = status_label(out[book.id]["effective_status"])
        if out[book.id]["effective_status"] == "char_extracting":
            out[book.id]["action_hint"] = f"{settings.default_char_extraction_model} извлекает персонажей по всей книге и собирает полный CHAR_MEMORY."
        elif out[book.id]["char_extraction_failed"] > 0 and out[book.id]["effective_status"] == "processing":
            out[book.id]["action_hint"] = "CHAR_EXTRACTION упал, но downstream продолжает работу с ослабленным контекстом. Проверь CHAR_MEMORY и качество speaker attribution."
        elif out[book.id]["effective_status"] == "stalled":
            out[book.id]["action_hint"] = "Задача зависла дольше допустимого окна. Проверь лог и нажми CONTINUE."
        elif out[book.id]["effective_status"] == "failed":
            out[book.id]["action_hint"] = "Есть failed job. Проверь лог книги и перезапусти пайплайн после правки."
        elif out[book.id]["polishing_skipped"] > 0 and out[book.id]["effective_status"] == "author_review":
            out[book.id]["action_hint"] = "Книга готова к review. Polishing сейчас работает как вторичный consistency-контур и мог быть автоматически пропущен."
        elif out[book.id]["effective_status"] == "author_review":
            out[book.id]["action_hint"] = "Книга готова к валидации."
        else:
            out[book.id]["action_hint"] = ""
    return out
