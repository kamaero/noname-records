"""Ранний сбой RQ тревожит администраторов, обработанный результат — только инициатора."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import BackgroundRun, TelegramAuthAccount, User, UserRole
from app.services import telegram
from app.worker_tasks import _fail_background_run, perform_consilium_task, perform_v2_pipeline_task


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for uid, chat in (("first", "101"), ("second", "202"), ("unlisted-admin", "303")):
            db.add(User(id=uid, login=uid, password_hash="unused", is_active="true"))
            db.add(UserRole(user_id=uid, role="admin"))
            db.add(TelegramAuthAccount(telegram_user_id=chat, user_id=uid,
                                       role="dictor", is_active="true"))
        db.commit()
    monkeypatch.setattr(settings, "telegram_notify_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "fake-system-token")
    monkeypatch.setattr(settings, "owner_telegram_id", "101")
    monkeypatch.setattr(settings, "telegram_system_admin_user_ids", "second")
    sent = []
    monkeypatch.setattr(telegram.requests, "post", lambda url, json, timeout:
                        sent.append(json) or SimpleNamespace(status_code=200))
    yield factory, sent
    engine.dispose()


def _run(factory, *, status="running"):
    with factory() as db:
        run = BackgroundRun(run_key="test", job_kind="consilium", status=status,
                            error_message="precise handler result", meta_json='{"language": "en"}')
        db.add(run)
        db.commit()
        return run.id


@pytest.mark.parametrize("status", ["queued", "running"])
def test_early_failure_notifies_both_admins_once_without_exception_details(env, status):
    factory, sent = env
    run_id = _run(factory, status=status)
    failure = RuntimeError("secret-token and private book text https://provider.test/key")

    _fail_background_run(factory, run_id, failure)
    _fail_background_run(factory, run_id, failure)

    assert [item["chat_id"] for item in sent] == ["101", "202"]
    assert sent[0]["text"] == sent[1]["text"]
    assert "secret-token" not in sent[0]["text"]
    assert "private book text" not in sent[0]["text"]
    assert "https://" not in sent[0]["text"]
    assert "RuntimeError" not in sent[0]["text"]
    with factory() as db:
        run = db.get(BackgroundRun, run_id)
        assert run.status == "failed"
        assert run.finished_at is not None
        assert run.finished_at == run.heartbeat_at


@pytest.mark.parametrize("status", ["done", "failed", "skipped", "cancelled", "stopped"])
def test_closed_handler_result_is_not_overwritten_or_broadcast(env, status):
    factory, sent = env
    run_id = _run(factory, status=status)
    _fail_background_run(factory, run_id, RuntimeError("outer exception"))
    assert sent == []
    with factory() as db:
        run = db.get(BackgroundRun, run_id)
        assert run.status == status
        assert run.error_message == "precise handler result"


def test_untracked_failure_does_not_create_a_system_alert(env):
    factory, sent = env
    _fail_background_run(factory, "", RuntimeError("no run"))
    _fail_background_run(factory, "missing", RuntimeError("no run"))
    assert sent == []


def test_notification_failure_does_not_replace_original_task_failure(env, monkeypatch):
    factory, _ = env
    run_id = _run(factory)
    original = RuntimeError("original task error")
    monkeypatch.setattr("app.worker_tasks.SessionLocal", factory)

    def fail_handler(**kwargs):
        raise original

    def fail_notify(*args):
        raise OSError("notification failure")

    monkeypatch.setattr("app.services.consilium_engine.run_consilium", fail_handler)
    monkeypatch.setattr(telegram, "send_system_alert", fail_notify)
    with pytest.raises(RuntimeError) as caught:
        perform_consilium_task("test-book", run_id=run_id)
    assert caught.value is original
    with factory() as db:
        assert db.get(BackgroundRun, run_id).status == "failed"


def test_handled_consilium_failure_keeps_initiator_notice_without_system_duplicate(env, monkeypatch):
    factory, sent = env
    run_id = _run(factory)
    monkeypatch.setattr("app.worker_tasks.SessionLocal", factory)

    def handled_failure(**kwargs):
        with factory() as db:
            run = db.get(BackgroundRun, run_id)
            run.status = "failed"
            run.error_message = "handled result"
            db.commit()
        kwargs["notify"]("Личный результат прогона")
        raise RuntimeError("handled failure")

    monkeypatch.setattr("app.services.consilium_engine.run_consilium", handled_failure)
    with pytest.raises(RuntimeError, match="handled failure"):
        perform_consilium_task("test-book", run_id=run_id, initiator_id="second")
    assert [(item["chat_id"], item["text"]) for item in sent] == [("202", "Личный результат прогона")]


def test_handled_v2_failure_is_recorded_without_a_second_admin_alert(env, monkeypatch):
    factory, sent = env
    run_id = _run(factory)
    monkeypatch.setattr("app.worker_tasks.SessionLocal", factory)

    def handled_failure(*args, **kwargs):
        with factory() as db:
            telegram.send_telegram_message(db, "Результат инициатору", chat_ids=["202"], direct=True)
        return SimpleNamespace(status="failed", error="handled v2 error")

    monkeypatch.setattr("app.v2.pipeline.run_book_pipeline", handled_failure)
    with pytest.raises(RuntimeError, match="handled v2 error"):
        perform_v2_pipeline_task("test-book", run_id=run_id, v2_run_id="test-v2-run", language="en")
    assert [(item["chat_id"], item["text"]) for item in sent] == [("202", "Результат инициатору")]
    with factory() as db:
        run = db.get(BackgroundRun, run_id)
        assert run.status == "failed"
        assert run.error_message == "handled v2 error"
