"""Постановка задачи и смена профиля сериализуются одной блокировкой книги."""
import json

from sqlalchemy import and_, or_

from app.models import BackgroundRun, ScriptBook, ScriptChapter
from app.v2.models import V2Run


def lock_book_profile(db, book_id: str = "", *, chapter_id: str = ""):
    # SQLite не поддерживает FOR UPDATE. Захватываем право записи до чтения,
    # иначе две транзакции одновременно разрешат смену языка и постановку работы.
    if db.get_bind().dialect.name == "sqlite":
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    if chapter_id:
        chapter = db.get(ScriptChapter, chapter_id)
        if chapter is None:
            return None
        book_id = chapter.book_id
    if db.get_bind().dialect.name == "sqlite":
        return db.get(ScriptBook, book_id)
    return db.query(ScriptBook).filter(ScriptBook.id == book_id).with_for_update().one_or_none()


def active_profile_job(db, book_id: str, *, kinds=("v2_pipeline", "consilium", "sound", "ambient")):
    if "v2_pipeline" in kinds:
        run = db.query(V2Run).filter(V2Run.book_id == book_id, V2Run.status.in_(("queued", "running"))).first()
        if run is not None:
            return run
    # Старые задания очереди могли не иметь строки v2_runs. Они тоже не разрешают
    # менять профиль до явного завершения/отмены, даже если язык ещё не был записан.
    return db.query(BackgroundRun).filter(
        BackgroundRun.job_kind.in_(kinds),
        or_(BackgroundRun.entity_id == book_id,
            and_(BackgroundRun.job_kind == "ambient", BackgroundRun.entity_id.in_(
                db.query(ScriptChapter.id).filter(ScriptChapter.book_id == book_id))),
            BackgroundRun.run_key.in_([f"{kind}:{book_id}" for kind in kinds] + [f"v2-pipeline:{book_id}"])),
        BackgroundRun.status.in_(("queued", "running")),
    ).first()


def background_run_language(session_factory, run_id: str, language: str | None = None) -> str:
    """Старый job без доказанного снимка не имеет права выбирать профиль по живой книге."""
    with session_factory() as db:
        row = db.get(BackgroundRun, run_id)
        try:
            meta = json.loads(row.meta_json or "{}") if row is not None else {}
        except (TypeError, ValueError):
            meta = {}
        stored = meta.get("language") if isinstance(meta, dict) else None
    if stored not in ("en", "ru"):
        raise RuntimeError("language_snapshot_missing: start a new run to select the current book profile")
    if language is not None and stored != language:
        raise RuntimeError("language_snapshot_mismatch")
    return stored
