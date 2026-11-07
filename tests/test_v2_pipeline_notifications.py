"""Итоги платного прогона не должны попадать другому администратору."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import BackgroundRun, ScriptBook, TelegramAuthAccount, User
from app.v2 import pipeline
from app.v2.models import V2Run
from app.workers.launcher import heartbeat_background_run


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    result = sessionmaker(bind=engine, autoflush=False)
    with result() as db:
        db.add(ScriptBook(id="book", title="Sample book", language="en", source_filename="book.txt", source_format="txt",
                          created_by_user_id="other"))
        db.add_all([
            User(id="initiator", login="initiator", password_hash="x"),
            User(id="other", login="other", password_hash="x"),
            User(id="inactive", login="inactive", password_hash="x"),
            User(id="disabled-user", login="disabled-user", password_hash="x", is_active="false"),
            TelegramAuthAccount(user_id="disabled-user", telegram_user_id="505", role="admin"),
            TelegramAuthAccount(user_id="initiator", telegram_user_id="202", role="admin"),
            TelegramAuthAccount(user_id="other", telegram_user_id="303", role="admin"),
            TelegramAuthAccount(user_id="inactive", telegram_user_id="404", role="admin", is_active="false"),
        ])
        db.commit()
    yield result
    engine.dispose()


@pytest.fixture
def sent(monkeypatch):
    from app.services import telegram

    monkeypatch.setattr(settings, "owner_telegram_id", "101")
    monkeypatch.setattr(settings, "telegram_notify_chat_ids", "101,303")
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    monkeypatch.setattr(settings, "telegram_notify_enabled", True)
    calls = []

    class Response:
        status_code = 200

    def post(url, *, json, timeout):
        calls.append(json)
        return Response()

    monkeypatch.setattr(telegram.requests, "post", post)
    return calls


def _tracked(factory, meta):
    with factory() as db:
        db.add(BackgroundRun(id="tracked", run_key="v2-pipeline:book", job_kind="v2_pipeline",
                             entity_type="script_book", entity_id="book", status="running",
                             meta_json=json.dumps(meta)))
        db.commit()


@pytest.mark.parametrize("failed", [False, True])
def test_worker_keeps_initiator_through_heartbeat_and_routes_both_outcomes(factory, sent, monkeypatch, failed):
    from app import worker_tasks

    _tracked(factory, {"by": "initiator", "steps": ["segment"], "force": True})
    with factory() as db:
        db.add(V2Run(id="v2-run", book_id="book", status="queued", language_snapshot="en"))
        db.commit()
    original = pipeline.run_book_pipeline

    def step(db, book, run, ctx):
        if failed:
            raise RuntimeError("Provider unavailable")

    def run(*args, **kwargs):
        return original(*args, **kwargs, step_functions={"segment": step})

    monkeypatch.setattr(pipeline, "run_book_pipeline", run)
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    if failed:
        with pytest.raises(RuntimeError, match="Provider unavailable"):
            worker_tasks.perform_v2_pipeline_task("book", run_id="tracked", v2_run_id="v2-run", steps=["segment"], language="en")
    else:
        worker_tasks.perform_v2_pipeline_task("book", run_id="tracked", v2_run_id="v2-run", steps=["segment"], language="en")
    assert [call["chat_id"] for call in sent] == ["202"]
    assert ("processing failed" if failed else "book processed") in sent[0]["text"]
    with factory() as db:
        tracked = db.get(BackgroundRun, "tracked")
        meta = json.loads(tracked.meta_json)
        assert meta["by"] == "initiator"
        assert meta["steps"] == ["segment"] and meta["force"] is True
        assert tracked.status == ("failed" if failed else "done")


@pytest.mark.parametrize("meta", [{}, {"by": ""}, None])
def test_legacy_run_without_initiator_notifies_only_owner(factory, sent, meta):
    if meta is not None:
        _tracked(factory, meta)
    pipeline.run_book_pipeline("book", steps=["segment"], session_factory=factory,
                               step_functions={"segment": lambda *args: None},
                               background_run_id="tracked" if meta is not None else "")
    assert [call["chat_id"] for call in sent] == ["101"]


@pytest.mark.parametrize("initiator", ["missing", "inactive", "disabled-user"])
def test_known_initiator_without_active_binding_does_not_fall_back_to_other_admins(factory, sent, initiator):
    _tracked(factory, {"by": initiator})
    pipeline.run_book_pipeline("book", steps=["segment"], session_factory=factory,
                               step_functions={"segment": lambda *args: None}, background_run_id="tracked")
    assert sent == []


@pytest.mark.parametrize("meta", ["bad-json", "[]"])
def test_heartbeat_tolerates_old_invalid_metadata(factory, meta):
    _tracked(factory, {})
    with factory() as db:
        db.get(BackgroundRun, "tracked").meta_json = meta
        db.commit()
    heartbeat_background_run(factory, BackgroundRun, run_key="v2-pipeline:book", meta={"phase": "segment"})
    with factory() as db:
        assert json.loads(db.get(BackgroundRun, "tracked").meta_json) == {"phase": "segment"}


def test_old_queued_worker_arguments_still_execute(factory, sent, monkeypatch):
    from app import worker_tasks

    original = pipeline.run_book_pipeline
    monkeypatch.setattr(pipeline, "run_book_pipeline", lambda *a, **kw: original(
        *a, **kw, step_functions={"segment": lambda *args: None}))
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    worker_tasks.perform_v2_pipeline_task("book", steps=["segment"])
    assert [call["chat_id"] for call in sent] == ["101"]
