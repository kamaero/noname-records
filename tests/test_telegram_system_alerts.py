"""Системная тревога доступна администраторам, без расширения обычной рассылки."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import ScriptBook, ScriptChapter, TelegramAuthAccount, User, UserRole
from app.services import telegram
from app.services.integrity_notice import notify_integrity_problem, notify_mirror_problem


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture()
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(settings, "telegram_notify_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "system-alert-test-token")
    monkeypatch.setattr(settings, "owner_telegram_id", "100")
    monkeypatch.setattr(settings, "telegram_system_admin_user_ids", " owner,200,300,400,500,600,deleted,no-telegram,200 ")
    monkeypatch.setattr(settings, "telegram_notify_chat_ids", "900,901")
    monkeypatch.setattr(telegram.requests, "post", lambda url, json, timeout:
                        messages.append(json) or SimpleNamespace(status_code=200))
    return messages


def _account(db, key, *, role="admin", active="true", telegram_active="true",
             telegram_role="dictor", linked=True, chat=None):
    db.add(User(id=key, login=key, password_hash="unused", is_active=active))
    db.add(UserRole(user_id=key, role=role))
    db.add(TelegramAuthAccount(telegram_user_id=chat or key, user_id=key if linked else "",
                               role=telegram_role, is_active=telegram_active))
    db.flush()


def test_system_alert_reaches_only_owner_and_explicitly_allowed_current_admins_once(db, sent):
    _account(db, "owner", chat=" 100 ")
    _account(db, "200")
    _account(db, "unlisted-admin", chat="800", telegram_role="admin")
    db.add(UserRole(user_id="200", role="admin"))
    _account(db, "300", role="author", telegram_role="admin")
    _account(db, "400", active="false")
    _account(db, "500", telegram_active="false")
    _account(db, "600", linked=False, telegram_role="admin")
    db.add(TelegramAuthAccount(telegram_user_id="700", user_id="deleted",
                               role="admin", is_active="true"))
    db.add(User(id="no-telegram", login="no-telegram", password_hash="unused"))
    db.add(UserRole(user_id="no-telegram", role="admin"))
    db.flush()

    assert telegram.send_system_alert(db, "Проверка целостности") == 2
    assert [message["chat_id"] for message in sent] == ["100", "200"]


def test_system_alert_without_owner_uses_admins_and_never_general_targets(db, sent, monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", "")
    _account(db, "200")
    assert telegram.send_system_alert(db, "Сбой") == 1
    assert [message["chat_id"] for message in sent] == ["200"]
    db.query(UserRole).delete()
    assert telegram.send_system_alert(db, "Сбой") == 0
    assert len(sent) == 1


def test_system_alert_without_database_can_still_reach_owner(sent):
    assert telegram.send_system_alert(None, "Сбой") == 1
    assert [message["chat_id"] for message in sent] == ["100"]


@pytest.mark.parametrize("setting,value", [
    ("telegram_notify_enabled", False),
    ("telegram_bot_token", ""),
])
def test_system_alert_respects_telegram_settings(db, sent, monkeypatch, setting, value):
    _account(db, "200")
    monkeypatch.setattr(settings, setting, value)
    assert telegram.send_system_alert(db, "Сбой") == 0
    assert sent == []


def test_failure_of_one_target_does_not_hide_alert_from_second_or_leak_token(db, sent, monkeypatch, caplog):
    _account(db, "200")

    def post(url, json, timeout):
        if json["chat_id"] == "100":
            raise RuntimeError(url)
        sent.append(json)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(telegram.requests, "post", post)
    assert telegram.send_system_alert(db, "Сбой") == 1
    assert [message["chat_id"] for message in sent] == ["200"]
    assert "system-alert-test-token" not in caplog.text


def test_personal_and_ordinary_notifications_keep_their_recipients(db, sent):
    _account(db, "200")
    telegram.send_telegram_message(db, "Книга обработана")
    telegram.send_telegram_message(db, "Письмо диктору", chat_ids=["300"], direct=True)
    assert [message["chat_id"] for message in sent] == ["100", "300"]


@pytest.mark.parametrize("notify,state,prefix", [
    (notify_integrity_problem, "missing", "⚠️ File integrity:"),
    (notify_integrity_problem, "mismatch", "⚠️ File integrity:"),
    (notify_mirror_problem, "mismatch", "⚠️ Mirror:"),
])
def test_existing_integrity_alerts_reach_both_admins(db, sent, monkeypatch, notify, state, prefix):
    from app.services import audio_integrity

    _account(db, "200")
    book = ScriptBook(title="Тест", source_filename="test.txt", source_format="txt")
    db.add(book)
    db.flush()
    chapter = ScriptChapter(book_id=book.id, chapter_index=1, chapter_title="Глава 1")
    db.add(chapter)
    db.flush()
    item = SimpleNamespace(role="Герой", actor_name="Диктор", canonical_filename="test.wav",
                           verify_state=state, mirror_state=state)
    monkeypatch.setattr(audio_integrity, "chapter_files", lambda db, chapter_id: [item])

    assert notify(db, chapter.id)
    assert [message["chat_id"] for message in sent] == ["100", "200"]
    assert all(message["text"].startswith(prefix) for message in sent)


@pytest.mark.parametrize("notify", [notify_integrity_problem, notify_mirror_problem])
def test_absent_chapter_sends_no_system_alert(db, sent, notify):
    assert not notify(db, "absent")
    assert sent == []


def test_empty_allowlist_defaults_to_owner_without_querying_other_admins(sent, monkeypatch):
    monkeypatch.setattr(settings, "telegram_system_admin_user_ids", " , ")
    assert telegram.send_system_alert(object(), "System issue") == 1
    assert [message["chat_id"] for message in sent] == ["100"]


def test_empty_allowlist_and_no_owner_never_fall_back_to_general_targets(db, sent, monkeypatch):
    _account(db, "200")
    monkeypatch.setattr(settings, "telegram_system_admin_user_ids", "")
    monkeypatch.setattr(settings, "owner_telegram_id", "")
    assert telegram.send_system_alert(db, "System issue") == 0
    assert sent == []


def test_allowlist_matches_stable_user_id_not_telegram_id(db, sent, monkeypatch):
    _account(db, "allowed-user", chat="444")
    monkeypatch.setattr(settings, "telegram_system_admin_user_ids", "444")
    telegram.send_system_alert(db, "System issue")
    assert [message["chat_id"] for message in sent] == ["100"]
    sent.clear()
    monkeypatch.setattr(settings, "telegram_system_admin_user_ids", " allowed-user,allowed-user ")
    telegram.send_system_alert(db, "System issue")
    assert [message["chat_id"] for message in sent] == ["100", "444"]
