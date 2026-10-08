"""Очередь внутри процесса: настольной версии не нужны Redis и отдельные воркеры."""
import threading
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import seat
from app.config import settings
from app.db import Base
from app.models import BackgroundRun, ScriptBook
from app.time_utils import utcnow_naive
from app.v2.models import V2Run
from app.workers import local_queue as lq

CALLS: list = []


def record_task(run_id=None, **kwargs):
    CALLS.append((run_id, kwargs))


def boom_task(run_id=None):
    raise RuntimeError("упало")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    CALLS.clear()
    monkeypatch.setattr(lq, "_LINES", {})
    monkeypatch.setattr(settings, "seat_mode", "one")


def _engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


def test_one_seat_follows_the_setting(monkeypatch):
    assert seat.one_seat() is True
    monkeypatch.setattr(settings, "seat_mode", "studio")
    assert seat.one_seat() is False


def test_task_from_dotted_path_and_from_function_runs_with_run_id():
    q = lq.local_queue("default")
    job = q.enqueue("tests.test_seat_queue.record_task", job_timeout=10, result_ttl=5, run_id="r1", book="b")
    q.enqueue(record_task, run_id="r2")
    assert q.join(5)
    assert job.id and CALLS == [("r1", {"book": "b"}), ("r2", {})]


def test_consilium_line_does_not_block_the_main_line():
    gate = threading.Event()
    lq.local_queue("consilium").enqueue(lambda run_id=None: gate.wait(5), run_id="slow")
    main = lq.local_queue("high")
    main.enqueue(record_task, run_id="fast")
    assert main.join(5) and CALLS == [("fast", {})]
    gate.set()


def test_high_default_low_share_one_line_in_order():
    assert lq.local_queue("high") is lq.local_queue("low") is lq.local_queue("default")


def test_a_failing_task_does_not_kill_the_line():
    q = lq.local_queue("default")
    q.enqueue(boom_task, run_id="x")
    q.enqueue(record_task, run_id="after")
    assert q.join(5) and CALLS == [("after", {})]


def test_get_queue_is_local_in_one_seat_and_rq_in_studio(monkeypatch):
    from app import redis_client
    assert isinstance(redis_client.get_queue("consilium"), lq.LocalQueue)
    monkeypatch.setattr(settings, "seat_mode", "studio")
    assert not isinstance(redis_client.get_queue("default"), lq.LocalQueue)


def test_studio_routes_consilium_to_its_own_rq_queue(monkeypatch):
    # До 08.10 «consilium» молча отдавал default: консилиум, звук и эмбиент шли в общий
    # воркер и держали сверку дублей, а worker-consilium простаивал.
    from app import redis_client
    monkeypatch.setattr(settings, "seat_mode", "studio")
    assert redis_client.get_queue("consilium").name == "consilium"
    assert redis_client.get_queue("default").name == "default"


def test_one_seat_never_touches_redis(monkeypatch):
    import redis
    attempts = []
    monkeypatch.setattr(redis.Connection, "connect", lambda self: attempts.append(1))
    from app.workers.launcher import enqueue_tracked_task
    factory = sessionmaker(bind=_engine())
    enqueue_tracked_task("default", record_task, factory, BackgroundRun, job_kind="t", run_key="t1")
    assert lq.local_queue("default").join(5)
    assert attempts == [] and len(CALLS) == 1


def test_interrupted_runs_are_marked_on_start():
    with sessionmaker(bind=_engine())() as db:
        db.add(BackgroundRun(run_key="a", job_kind="asr", status="running", heartbeat_at=utcnow_naive()))
        db.add(BackgroundRun(run_key="b", job_kind="asr", status="done", heartbeat_at=utcnow_naive()))
        db.add(ScriptBook(id="b1", title="К", source_filename="k.txt", source_format="txt", status="processing"))
        db.add(V2Run(id="v1", book_id="b1", status="queued"))
        db.add(V2Run(id="v2", book_id="b1", status="running", updated_at=utcnow_naive() - timedelta(minutes=1)))
        db.commit()
        assert seat.mark_interrupted_runs(db) == 3
        assert {r.status for r in db.query(V2Run)} == {"failed"}
        assert db.query(BackgroundRun).filter_by(run_key="b").one().status == "done"
        assert "закрыли" in db.query(V2Run).filter_by(id="v1").one().error
        assert db.get(ScriptBook, "b1").status == "stopped"
