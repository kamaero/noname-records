"""Сторож сообщает смену состояния без новой NFS-пробы и повторной успешной доставки."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import TelegramAuthAccount, User, UserRole
from app.services import nas_health, nas_notice, telegram
from app.workers import mirror


@pytest.fixture()
def env(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add(User(id="second", login="second", password_hash="unused", is_active="true"))
        db.add(UserRole(user_id="second", role="admin"))
        db.add(TelegramAuthAccount(telegram_user_id="202", user_id="second", role="dictor", is_active="true"))
        db.add(User(id="unlisted-admin", login="unlisted-admin", password_hash="unused", is_active="true"))
        db.add(UserRole(user_id="unlisted-admin", role="admin"))
        db.add(TelegramAuthAccount(telegram_user_id="303", user_id="unlisted-admin", role="admin", is_active="true"))
        db.commit()
    monkeypatch.setattr(settings, "telegram_notify_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "fake-token")
    monkeypatch.setattr(settings, "owner_telegram_id", "101")
    monkeypatch.setattr(settings, "telegram_system_admin_user_ids", "second")
    sent = []
    monkeypatch.setattr(telegram.requests, "post", lambda url, json, timeout:
                        sent.append(json) or SimpleNamespace(status_code=200))
    now = [0.0]
    notice = nas_notice.NasTransitionNotice(grace_seconds=30, retry_seconds=60, clock=lambda: now[0])
    yield factory, sent, now, notice
    engine.dispose()


def test_first_healthy_probe_is_quiet_and_each_failure_and_recovery_is_sent_once(env):
    factory, sent, _, notice = env
    with factory() as db:
        for online in (True, True, False, False, False, True, True):
            notice.observe(db, online)
    assert [item["chat_id"] for item in sent] == ["101", "202", "101", "202"]
    assert sent[0]["text"] != sent[2]["text"]
    assert all("fake-token" not in item["text"] for item in sent)


def test_unknown_at_start_has_grace_but_stale_probe_after_known_state_is_reported(env):
    factory, sent, now, notice = env
    with factory() as db:
        notice.observe(db, None)
        now[0] = 29
        notice.observe(db, None)
        assert sent == []
        now[0] = 30
        notice.observe(db, None)
        notice.observe(db, None)
        assert len(sent) == 2
        notice.observe(db, True)
        assert len(sent) == 4
        notice.observe(db, None)
        assert len(sent) == 6


def test_successful_recipient_is_not_repeated_when_second_delivery_fails(env, monkeypatch):
    factory, sent, now, notice = env
    attempts = []

    def post(url, json, timeout):
        attempts.append(json["chat_id"])
        if json["chat_id"] == "202" and now[0] < 60:
            return SimpleNamespace(status_code=500)
        sent.append(json)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(telegram.requests, "post", post)
    with factory() as db:
        notice.observe(db, False)
        now[0] = 59
        notice.observe(db, False)
        assert attempts == ["101", "202"]
        now[0] = 60
        notice.observe(db, False)
        assert attempts == ["101", "202", "202"]
        notice.observe(db, False)
    assert [item["chat_id"] for item in sent] == ["101", "202"]


def test_new_incident_and_process_restart_each_rearm_delivery(env):
    factory, sent, now, notice = env
    with factory() as db:
        notice.observe(db, False)
        notice.observe(db, True)
        notice.observe(db, False)
        assert len(sent) == 6
        restarted = nas_notice.NasTransitionNotice(grace_seconds=30, clock=lambda: now[0])
        restarted.observe(db, False)
    assert len(sent) == 8


def test_current_administrator_rights_are_rechecked_before_retry(env, monkeypatch):
    factory, sent, now, notice = env
    monkeypatch.setattr(nas_notice, "send_telegram_message", lambda db, text, **kw: 0)
    with factory() as db:
        notice.observe(db, False)
        db.query(UserRole).delete()
        db.commit()
        now[0] = 60
        monkeypatch.setattr(nas_notice, "send_telegram_message", telegram.send_telegram_message)
        notice.observe(db, False)
    assert [item["chat_id"] for item in sent] == ["101"]


class StopLoop(BaseException):
    pass


@pytest.mark.parametrize("configured", [False, True])
def test_existing_mirror_loop_only_notifies_for_configured_nas_without_probing(env, monkeypatch, configured):
    factory, sent, _, _ = env
    runtime = SimpleNamespace(mirror_interval_minutes=5, nas_probe_stale_seconds=30,
                              audio_nas_path="/unreachable/nas" if configured else "")
    monkeypatch.setattr(mirror, "heartbeat_background_run", lambda *a, **kw: None)
    monkeypatch.setattr(mirror, "mirror_once", lambda db: {"mirrored": 0, "failed": 0, "left": 0})
    monkeypatch.setattr(mirror, "purge_deleted_once", lambda db: {"purged": 0, "missing": 0, "reused": 0, "left": 0})
    monkeypatch.setattr(mirror, "archive_pending_chapter_sessions", lambda db: {"archived": 0})

    def forbidden_probe(*args, **kwargs):
        pytest.fail("путь уведомления выполнил NFS-пробу")

    monkeypatch.setattr(nas_health, "probe_once", forbidden_probe)
    nas_health.record_probe(False)
    loops = []

    def wait(*args, **kwargs):
        loops.append(1)
        if len(loops) == 2:
            raise StopLoop

    monkeypatch.setattr(mirror.time, "sleep", wait)
    with pytest.raises(StopLoop):
        mirror.mirror_loop(settings=runtime, session_factory=factory, background_run_cls=object)
    assert [item["chat_id"] for item in sent] == (["101", "202"] if configured else [])


def test_notice_exception_does_not_prevent_mirroring(env, monkeypatch, caplog):
    factory, _, _, _ = env
    runtime = SimpleNamespace(mirror_interval_minutes=5, audio_nas_path="/nas")
    seen = []

    def fail_notice(*args, **kwargs):
        raise RuntimeError("secret-url-token")

    monkeypatch.setattr(mirror.NasTransitionNotice, "observe", fail_notice)
    monkeypatch.setattr(mirror, "heartbeat_background_run", lambda *a, **kw: None)

    def stop_after_copy(db):
        seen.append("copy")
        raise StopLoop

    monkeypatch.setattr(mirror, "mirror_once", stop_after_copy)
    with pytest.raises(StopLoop):
        mirror.mirror_loop(settings=runtime, session_factory=factory, background_run_cls=object)
    assert seen == ["copy"]
    assert "secret-url-token" not in caplog.text
