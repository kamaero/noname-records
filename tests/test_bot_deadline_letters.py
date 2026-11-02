"""Кнопки под письмами бота (пакет B, 05.10)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, RoleDeadline, ScriptBook, ScriptChapter, TelegramAuthAccount, User, UserRole
from app.services import bot_out, role_approval, role_deadlines, telegram

NOW = datetime(2026, 10, 5, 9, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Карта Приозерья", display_title="Карта Приозерья",
                               source_filename="x", source_format="docx", total_chars=0, author_sheets_x1000=0,
                               chapter_count=1, has_chapters="true", status="processing"))
        session.add(ScriptChapter(id="b1-1", book_id="b1", chapter_index=1, chapter_title="Глава 1"))
        session.add(Character(id="c1", book_id="b1", name="Дракон", actor_name="", appears_in="1"))
        session.add(User(id="u41", login="tg_41", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
        session.add(UserRole(user_id="u41", role="dictor"))
        session.add(TelegramAuthAccount(telegram_user_id="41", role="dictor", access_scope="full",
                                        display_name="Ветрова Ольга", is_active="true", user_id="u41"))
        session.commit()
        yield session


def _letters(db, monkeypatch, actor):
    sent = []
    monkeypatch.setattr(role_approval, "send_telegram_message",
                        lambda db, text, chat_ids=None, direct=False, **kw: sent.append((text, kw)) or 1)
    role_approval.notify_role_approved(db, book_id="b1", book_title="Карта Приозерья", role="Дракон",
                                       actor_name=actor)
    return sent


def _data(markup):
    return [b["callback_data"] for row in markup["inline_keyboard"] for b in row]


def test_the_audition_letter_has_take_decline_and_time_buttons(db, monkeypatch):
    (text, kw), = _letters(db, monkeypatch, "Ветрова Ольга?")
    assert _data(kw["reply_markup"]) == ["dl:y:c1", "dl:n:c1", "dl:t:c1"]


def test_the_approval_letter_has_only_the_time_button(db, monkeypatch):
    (text, kw), = _letters(db, monkeypatch, "Ветрова Ольга")
    assert _data(kw["reply_markup"]) == ["dl:t:c1"]


def test_the_relay_has_no_buttons(db, monkeypatch):
    sent = _letters(db, monkeypatch, "Нет Такого?")
    assert sent and all("reply_markup" not in kw for _, kw in sent)


def test_send_telegram_message_passes_reply_markup(monkeypatch):
    posted = []

    class _Resp:
        ok, status_code = True, 200

        def json(self):
            return {"ok": True}

    monkeypatch.setattr(telegram.settings, "telegram_notify_enabled", True)
    monkeypatch.setattr(telegram.settings, "telegram_bot_token", "t")
    monkeypatch.setattr(telegram.requests, "post", lambda url, json, timeout: posted.append(json) or _Resp())
    telegram.send_telegram_message(None, "x", chat_ids=["41"], direct=True, reply_markup=bot_out.time_markup("c1"))
    telegram.send_telegram_message(None, "y", chat_ids=["41"], direct=True)
    assert posted[0]["reply_markup"] == bot_out.time_markup("c1") and "reply_markup" not in posted[1]


def test_reminders_carry_the_time_button_and_old_senders_still_work(db):
    db.add(RoleDeadline(id="d1", character_id="c1", book_id="b1", actor_name="Ветрова Ольга", kind="audition",
                        due_at=NOW + timedelta(hours=5), created_at=NOW))
    db.query(Character).filter_by(id="c1").update({"actor_name": "Ветрова Ольга?"})
    db.commit()
    with_buttons = []
    role_deadlines.run_pass(db, now=NOW, send=lambda chat, text: True,
                            send_buttons=lambda chat, text, markup: with_buttons.append(markup) or True)
    assert with_buttons == [bot_out.time_markup("c1")]
    db.query(RoleDeadline).update({"reminded_before_at": None})
    db.commit()
    plain = []
    role_deadlines.run_pass(db, now=NOW, send=lambda chat, text: plain.append(text) or True)
    assert len(plain) == 1
