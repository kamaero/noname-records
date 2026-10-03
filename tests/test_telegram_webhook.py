import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import telegram_webhook
from app.config import settings
from app.db import Base
from app.main import app
from app.models import TelegramAuthAccount, User, UserRole
from app.services import telegram
from app.services.login_identity import TELEGRAM_PASSWORD_HASH

SECRET = "s3cret-webhook"
HDR = {"X-Telegram-Bot-Api-Secret-Token": SECRET}


@pytest.fixture()
def sent(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(telegram_webhook, "SessionLocal", factory)
    monkeypatch.setattr(settings, "telegram_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "telegram_notify_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-token")
    calls = []
    monkeypatch.setattr(telegram_webhook, "call_bot_api", lambda method, payload: calls.append((method, payload)) or True)
    with factory() as db:
        db.add(User(id="u1", login="olga", password_hash=TELEGRAM_PASSWORD_HASH, display_name="Ольга", is_active="true"))
        db.add(UserRole(id="r1", user_id="u1", role="dictor"))
        db.add(TelegramAuthAccount(telegram_user_id="42", role="dictor", access_scope="full",
                                   display_name="Ольга", is_active="true", user_id="u1"))
        db.commit()
    return calls


def _press_button():
    return {"update_id": 1, "message": {"message_id": 1, "from": {"id": 42}, "chat": {"id": 42, "type": "private"},
                                        "text": "🔑 Логин и пароль"}}


def test_without_a_configured_secret_the_address_does_not_exist(monkeypatch):
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")
    assert TestClient(app).post("/api/telegram/webhook", json={}, headers=HDR).status_code == 404


def test_a_wrong_secret_is_refused(sent):
    r = TestClient(app).post("/api/telegram/webhook", json=_press_button(), headers={"X-Telegram-Bot-Api-Secret-Token": "x"})
    assert r.status_code == 403 and sent == []


def test_the_reply_goes_to_the_sender_even_with_an_owner_configured(sent, monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", "900000100")
    r = TestClient(app).post("/api/telegram/webhook", json=_press_button(), headers=HDR)
    assert r.status_code == 200
    assert [(m, p["chat_id"]) for m, p in sent] == [("sendMessage", 42)]


def test_a_failure_inside_still_answers_200_and_logs_only_the_type(sent, monkeypatch, caplog):
    def boom(db, update, *, now):
        raise RuntimeError("пароль-в-тексте-ошибки")
    monkeypatch.setattr(telegram_webhook, "handle_update", boom)
    with caplog.at_level(logging.WARNING):
        r = TestClient(app).post("/api/telegram/webhook", json=_press_button(), headers=HDR)
    assert r.status_code == 200
    assert "RuntimeError" in caplog.text and "пароль-в-тексте-ошибки" not in caplog.text


def test_a_body_that_is_not_json_is_answered_200(sent):
    r = TestClient(app).post("/api/telegram/webhook", content=b"not json", headers=HDR)
    assert r.status_code == 200 and sent == []


def test_call_bot_api_stays_silent_when_telegram_is_switched_off():
    assert telegram.call_bot_api("sendMessage", {"chat_id": 1, "text": "x"}) is False


@pytest.mark.parametrize("enabled,token", [(False, "test-token"), (True, "")])
def test_without_a_way_to_answer_the_address_is_off_and_no_password_changes(sent, monkeypatch, enabled, token):
    # Ревью 01.10: иначе пароль менялся бы, а сообщение с ним не уходило — человек снаружи.
    monkeypatch.setattr(settings, "telegram_notify_enabled", enabled)
    monkeypatch.setattr(settings, "telegram_bot_token", token)
    r = TestClient(app).post("/api/telegram/webhook", json=_press_button(), headers=HDR)
    assert r.status_code == 404 and sent == []


def test_processing_and_sending_run_off_the_event_loop(sent, monkeypatch):
    # Ревью 01.10: синхронные SQLAlchemy и requests в async-обработчике держали бы весь сайт,
    # пока Telegram не ответит.
    seen = []

    async def fake_threadpool(func, *args, **kwargs):
        seen.append(func.__name__)
        return func(*args, **kwargs)
    monkeypatch.setattr(telegram_webhook, "run_in_threadpool", fake_threadpool)
    TestClient(app).post("/api/telegram/webhook", json=_press_button(), headers=HDR)
    assert seen == ["process_update"] and len(sent) == 1


def test_enqueue_intro_is_run_here_and_not_sent_to_telegram(sent, monkeypatch):
    queued = []
    monkeypatch.setattr(telegram_webhook, "enqueue_intro_followup", lambda tid: queued.append(tid) or "job-1")
    from app.services import bot_dialog
    from app.services.bot_dialog import Outgoing
    monkeypatch.setattr(telegram_webhook, "handle_update", lambda db, update, *, now: [
        Outgoing("sendMessage", {"chat_id": 42, "text": "Готово"}),
        Outgoing("enqueue_intro", {"telegram_user_id": "42"}),
    ])
    TestClient(app).post("/api/telegram/webhook", json=_press_button(), headers=HDR)
    assert queued == ["42"] and [m for m, _ in sent] == ["sendMessage"]


def test_a_failed_enqueue_is_logged_by_type_and_still_200(sent, monkeypatch, caplog):
    def down(tid):
        raise ConnectionError("redis://secret@host")
    monkeypatch.setattr(telegram_webhook, "enqueue_intro_followup", down)
    from app.services.bot_dialog import Outgoing
    monkeypatch.setattr(telegram_webhook, "handle_update", lambda db, update, *, now: [
        Outgoing("enqueue_intro", {"telegram_user_id": "42"})])
    with caplog.at_level(logging.WARNING):
        r = TestClient(app).post("/api/telegram/webhook", json=_press_button(), headers=HDR)
    assert r.status_code == 200 and "ConnectionError" in caplog.text and "secret@host" not in caplog.text


def test_an_enqueue_that_returns_nothing_is_logged(sent, monkeypatch, caplog):
    # Ревью 01.10: enqueue_tracked_task не бросает, а возвращает None — сбой был не виден.
    from app.services.bot_dialog import Outgoing
    monkeypatch.setattr(telegram_webhook, "enqueue_intro_followup", lambda tid: None)
    monkeypatch.setattr(telegram_webhook, "handle_update", lambda db, update, *, now: [
        Outgoing("enqueue_intro", {"telegram_user_id": "42"})])
    with caplog.at_level(logging.WARNING):
        TestClient(app).post("/api/telegram/webhook", json=_press_button(), headers=HDR)
    assert "не поставлена" in caplog.text and "42" in caplog.text


def test_a_broadcast_is_delivered_here_and_the_admin_gets_the_summary(sent, monkeypatch):
    from app.services.bot_dialog import Outgoing
    calls = []
    monkeypatch.setattr(telegram_webhook, "deliver_broadcast",
                        lambda db, bc_id, *, send: calls.append(bc_id) or (send("41", "письмо") and "Ушло: 1."))
    monkeypatch.setattr(telegram_webhook, "handle_update", lambda db, update, *, now: [
        Outgoing("broadcast", {"id": "abc12345", "admin_chat": 900000100})])
    TestClient(app).post("/api/telegram/webhook", json=_press_button(), headers=HDR)
    assert calls == ["abc12345"]
    assert [(m, p["chat_id"], p["text"]) for m, p in sent] == [("sendMessage", "41", "письмо"),
                                                               ("sendMessage", 900000100, "Ушло: 1.")]
