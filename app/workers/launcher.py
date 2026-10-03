import logging
import threading
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from app.redis_client import get_queue
from app.time_utils import utcnow_naive

logger = logging.getLogger(__name__)

_STALE_BACKGROUND_RUN_GRACE_SECONDS = 30

_threads_lock = threading.Lock()
_threads: dict[str, threading.Thread] = {}


def get_named_thread(name: str) -> threading.Thread | None:
    with _threads_lock:
        return _threads.get(name)


def start_named_thread(
    name: str,
    target: Callable[..., Any],
    *args: Any,
    daemon: bool = True,
    dedupe: bool = True,
    kwargs: dict[str, Any] | None = None,
) -> bool:
    with _threads_lock:
        existing = _threads.get(name)
        if dedupe and existing and existing.is_alive():
            return False

        def _runner() -> None:
            try:
                target(*args, **(kwargs or {}))
            except Exception:
                logger.exception("Background thread crashed: %s", name)
                raise
            finally:
                with _threads_lock:
                    current = _threads.get(name)
                    if current is thread:
                        _threads.pop(name, None)

        thread = threading.Thread(target=_runner, daemon=daemon, name=name)
        _threads[name] = thread
        thread.start()
        return True


def start_tracked_thread(
    name: str,
    target: Callable[..., Any],
    *args: Any,
    session_factory: Callable[[], Any],
    background_run_cls,
    job_kind: str,
    entity_type: str = "",
    entity_id: str = "",
    run_key: str | None = None,
    meta: dict[str, Any] | None = None,
    daemon: bool = True,
    dedupe: bool = True,
    kwargs: dict[str, Any] | None = None,
) -> bool:
    effective_key = (run_key or name).strip() or name

    with session_factory() as db:
        existing = db.query(background_run_cls).filter(
            background_run_cls.run_key == effective_key,
            background_run_cls.status == "running",
        ).order_by(background_run_cls.created_at.desc()).first()
        if dedupe and existing:
            existing_thread_name = str(getattr(existing, "thread_name", "") or "").strip()
            existing_thread = get_named_thread(existing_thread_name) if existing_thread_name else None
            if existing_thread and existing_thread.is_alive():
                logger.warning(
                    "Фоновый поток не запущен (run_key=%s): в этом процессе уже работает поток %s",
                    effective_key, existing_thread_name,
                )
                return False
            # Инцидент «сторож NAS не пережил рестарт сервиса»: цикл сторожа вечен, а
            # поток — daemon, поэтому строка `running` предыдущего процесса никогда не
            # закрывается, но пульс в неё пишется каждые 15 с. После обычного
            # `systemctl restart` она выглядит свежее 30-секундной льготы, и стартер
            # молча возвращал False — вся защита от мёртвого NAS была инертна с первого
            # же рестарта. Возраст пульса тут вообще не показатель: поток не переживает
            # свой процесс, значит строка, чьего `thread_name` нет среди живых потоков
            # ЭТОГО процесса, писана мертвецом — какой бы свежей она ни выглядела.
            # Цена принята сознательно: два uvicorn-воркера завели бы по сторожу, но
            # процесс у нас один.
            existing.status = "failed"
            existing.error_message = "stale_background_run_replaced"
            existing.finished_at = utcnow_naive()
            existing.heartbeat_at = existing.finished_at
            db.commit()
        run = background_run_cls(
            run_key=effective_key,
            job_kind=job_kind,
            entity_type=(entity_type or "").strip(),
            entity_id=(entity_id or "").strip(),
            status="running",
            thread_name=name,
            started_at=utcnow_naive(),
            heartbeat_at=utcnow_naive(),
            meta_json=_safe_meta_json(meta),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

    def _tracked_target() -> None:
        try:
            target(*args, **(kwargs or {}))
            _finish_background_run(session_factory, background_run_cls, run_id, "done", "")
        except Exception as exc:
            _finish_background_run(
                session_factory,
                background_run_cls,
                run_id,
                "failed",
                f"{type(exc).__name__}: {str(exc)[:220]}",
            )
            raise

    started = start_named_thread(name, _tracked_target, daemon=daemon, dedupe=dedupe)
    if not started:
        logger.warning(
            "Фоновый поток не запущен (run_key=%s): поток %s уже зарегистрирован в этом процессе",
            effective_key, name,
        )
        _finish_background_run(session_factory, background_run_cls, run_id, "skipped", "duplicate_launch_suppressed")
    return started


def heartbeat_background_run(
    session_factory: Callable[[], Any],
    background_run_cls,
    *,
    run_key: str,
    status: str = "running",
    meta: dict[str, Any] | None = None,
) -> None:
    with session_factory() as db:
        run = db.query(background_run_cls).filter(
            background_run_cls.run_key == run_key,
            background_run_cls.status == "running",
        ).order_by(background_run_cls.created_at.desc()).first()
        if not run:
            return
        run.status = status
        run.heartbeat_at = utcnow_naive()
        if meta is not None:
            run.meta_json = _safe_meta_json(meta)
        db.commit()


def _finish_background_run(
    session_factory: Callable[[], Any],
    background_run_cls,
    run_id: str,
    status: str,
    error_message: str,
) -> None:
    with session_factory() as db:
        run = db.query(background_run_cls).filter(background_run_cls.id == run_id).first()
        if not run:
            return
        run.status = status
        run.error_message = (error_message or "")[:255]
        run.finished_at = utcnow_naive()
        run.heartbeat_at = run.finished_at
        db.commit()


def _safe_meta_json(meta: dict[str, Any] | None) -> str:
    if not meta:
        return "{}"
    try:
        import json

        return json.dumps(meta, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def enqueue_tracked_task(
    queue_name: str,
    func_ref: str | Callable,
    session_factory: Callable[[], Any],
    background_run_cls,
    *,
    job_kind: str,
    entity_type: str = "",
    entity_id: str = "",
    run_key: str | None = None,
    meta: dict[str, Any] | None = None,
    dedupe: bool = True,
    **kwargs
) -> str | None:
    """
    Enqueues a task to RQ and creates a BackgroundRun record.
    Returns the job ID or None if deduplicated.
    """
    effective_key = (run_key or job_kind).strip() or job_kind
    
    with session_factory() as db:
        if dedupe:
            existing = db.query(background_run_cls).filter(
                background_run_cls.run_key == effective_key,
                background_run_cls.status.in_(["queued", "running"]),
            ).order_by(background_run_cls.created_at.desc()).first()
            
            if existing:
                heartbeat_at = getattr(existing, "heartbeat_at", None) or getattr(existing, "started_at", None) or getattr(existing, "created_at", None)
                fresh_cutoff = utcnow_naive() - timedelta(seconds=_STALE_BACKGROUND_RUN_GRACE_SECONDS)
                
                # If it's queued or running and fresh, skip
                if heartbeat_at and heartbeat_at >= fresh_cutoff:
                    logger.info(f"Skipping duplicate task enqueue: {effective_key}")
                    return None
                
                # Mark old run as failed
                existing.status = "failed"
                existing.error_message = "stale_background_run_replaced_by_queue"
                existing.finished_at = utcnow_naive()
                db.commit()

        run = background_run_cls(
            run_key=effective_key,
            job_kind=job_kind,
            entity_type=(entity_type or "").strip(),
            entity_id=(entity_id or "").strip(),
            status="queued",
            thread_name=f"rq:{queue_name}",
            started_at=None,
            heartbeat_at=utcnow_naive(),
            meta_json=_safe_meta_json(meta),
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        run_id = run.id

    try:
        q = get_queue(queue_name)
        # We pass run_id as a keyword argument to the task function
        job = q.enqueue(
            func_ref,
            # Big dense books (e.g. radio-plays) run finals sequentially and can
            # take far longer than 4h; the RQ job_timeout was killing the pipeline
            # task mid-run and leaving the book stalled/idle. Raise to 12h.
            job_timeout=3600 * 48,  # 48h — slow deepseek-v4-pro daytime finals (~70min/ch) can exceed 24h on big books
            result_ttl=86400,
            run_id=run_id,
            **kwargs
        )
        return job.id
    except Exception as exc:
        logger.exception(f"Failed to enqueue task {effective_key}")
        # Mark as failed immediately
        with session_factory() as db:
            run = db.query(background_run_cls).filter(background_run_cls.id == run_id).first()
            if run:
                run.status = "failed"
                run.error_message = f"enqueue_failed: {str(exc)}"
                run.finished_at = utcnow_naive()
                db.commit()
        return None
