import os
import sys
import threading
from datetime import timedelta
from tempfile import NamedTemporaryFile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db import Base
from app.models import BackgroundRun
from app.time_utils import utcnow_naive
from app.workers.launcher import get_named_thread, start_named_thread, start_tracked_thread


def test_start_tracked_thread_replaces_stale_running_row_without_live_thread() -> None:
    with NamedTemporaryFile(suffix=".db") as tmp:
        engine = create_engine(f"sqlite:///{tmp.name}")
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)

        with SessionLocal() as db:
            stale_started = utcnow_naive() - timedelta(minutes=10)
            db.add(
                BackgroundRun(
                    id="run-old",
                    run_key="book-pipeline:book-1",
                    job_kind="book_pipeline",
                    entity_type="script_book",
                    entity_id="book-1",
                    status="running",
                    thread_name="ghost-thread",
                    started_at=stale_started,
                    heartbeat_at=stale_started,
                )
            )
            db.commit()

        launched: list[str] = []

        def runner(book_id: str) -> None:
            launched.append(book_id)

        started = start_tracked_thread(
            "book-pipeline-book-1",
            runner,
            "book-1",
            session_factory=SessionLocal,
            background_run_cls=BackgroundRun,
            job_kind="book_pipeline",
            entity_type="script_book",
            entity_id="book-1",
            run_key="book-pipeline:book-1",
            meta={"book_id": "book-1"},
        )

        assert started is True

        # Join the spawned daemon thread so its background_run writes complete BEFORE the
        # NamedTemporaryFile is deleted on context exit — otherwise the thread races the
        # teardown and writes to a gone/readonly DB (intermittent PytestUnhandledThreadWarning).
        thread = get_named_thread("book-pipeline-book-1")
        if thread is not None:
            thread.join(timeout=5)
            assert not thread.is_alive()

        with SessionLocal() as db:
            rows = (
                db.query(BackgroundRun)
                .filter(BackgroundRun.run_key == "book-pipeline:book-1")
                .order_by(BackgroundRun.created_at.asc())
                .all()
            )

        assert len(rows) == 2
        assert rows[0].status == "failed"
        assert rows[0].error_message == "stale_background_run_replaced"
        assert rows[1].status in {"running", "done"}
        assert launched == ["book-1"]


def _running_row(SessionLocal, *, heartbeat_age_seconds: int, thread_name: str) -> None:
    """Строка `running` в базе — след предыдущего запуска сторожа."""
    with SessionLocal() as db:
        moment = utcnow_naive() - timedelta(seconds=heartbeat_age_seconds)
        db.add(
            BackgroundRun(
                id=f"run-{thread_name}",
                run_key="nas-probe-loop",
                job_kind="nas_probe_loop",
                entity_type="system",
                entity_id="nas-probe",
                status="running",
                thread_name=thread_name,
                started_at=moment,
                heartbeat_at=moment,
            )
        )
        db.commit()


def test_a_fresh_row_of_a_dead_process_does_not_block_the_watchdog() -> None:
    """Инцидент «сторож не пережил рестарт сервиса».

    Цикл сторожа вечен, поток — daemon: строка `running` никогда не закрывается, а
    пульс в неё пишется каждые 15 с. После обычного `systemctl restart` она выглядит
    свежее 30-секундной льготы, и стартер молча отказывался поднимать сторож — вся
    защита от мёртвого NAS была инертна с первого же рестарта. Потоки не переживают
    процесс: раз имени нет среди живых потоков ЭТОГО процесса, писал её мертвец.
    """
    with NamedTemporaryFile(suffix=".db") as tmp:
        engine = create_engine(f"sqlite:///{tmp.name}")
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)
        _running_row(SessionLocal, heartbeat_age_seconds=5, thread_name="nas-probe-worker")

        launched = threading.Event()

        started = start_tracked_thread(
            "nas-probe-worker",
            lambda: launched.set(),
            session_factory=SessionLocal,
            background_run_cls=BackgroundRun,
            job_kind="nas_probe_loop",
            entity_type="system",
            entity_id="nas-probe",
            run_key="nas-probe-loop",
            meta={},
        )

        assert started is True
        assert launched.wait(timeout=5) is True
        thread = get_named_thread("nas-probe-worker")
        if thread is not None:
            thread.join(timeout=5)


def test_a_live_thread_of_this_process_still_blocks_a_second_watchdog() -> None:
    """Дедупликация никуда не делась: два сторожа в одном процессе — две пробы,
    которые вдвоём же и повиснут на мёртвом монтировании."""
    with NamedTemporaryFile(suffix=".db") as tmp:
        engine = create_engine(f"sqlite:///{tmp.name}")
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)

        release = threading.Event()
        assert start_named_thread("nas-probe-worker-live", lambda: release.wait(timeout=10)) is True
        _running_row(SessionLocal, heartbeat_age_seconds=5, thread_name="nas-probe-worker-live")

        try:
            started = start_tracked_thread(
                "nas-probe-worker-live",
                lambda: None,
                session_factory=SessionLocal,
                background_run_cls=BackgroundRun,
                job_kind="nas_probe_loop",
                entity_type="system",
                entity_id="nas-probe",
                run_key="nas-probe-loop",
                meta={},
            )
            assert started is False
        finally:
            release.set()
            thread = get_named_thread("nas-probe-worker-live")
            if thread is not None:
                thread.join(timeout=5)
