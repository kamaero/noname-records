"""Письма о пробе и утверждении: срок, стоимость, ссылки на «Помощь» и первые шаги."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import Character, CharacterBudgetSnapshot, ScriptBook, TelegramAuthAccount, User, UserRole
from app.services import role_approval, role_deadlines


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "https://studio.example")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="СВ3", display_title="СВ3", source_filename="x.docx", source_format="docx",
                               total_chars=0, author_sheets_x1000=0, chapter_count=1, has_chapters="true",
                               status="processing"))
        session.add(Character(id="c1", book_id="b1", name="Куйбу", aliases="", character_color=""))
        session.add(User(id="u1", login="olga", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
        session.add(UserRole(user_id="u1", role="dictor"))
        session.add(TelegramAuthAccount(telegram_user_id="42", role="dictor", access_scope="full",
                                        display_name="Ветрова Ольга", is_active="true", user_id="u1"))
        session.commit()
        yield session


def _sent(db, monkeypatch, actor, cost=0):
    if cost:
        db.add(CharacterBudgetSnapshot(book_id="b1", character_id="c1", total_rub=cost))
        db.commit()
    texts = []
    monkeypatch.setattr(role_approval, "send_telegram_message",
                        lambda db, text, chat_ids=None, direct=False, **kw: texts.append(text) or 1)
    monkeypatch.setattr(role_approval, "utcnow_naive", lambda: datetime(2026, 10, 1, 15, 0, 0), raising=False)
    role_approval.notify_role_approved(db, book_id="b1", book_title="СВ3", role="Куйбу", actor_name=actor)
    return texts[0]


def test_the_audition_letter_has_48_hours_cost_and_links(db, monkeypatch):
    text = _sent(db, monkeypatch, "Ветрова Ольга?", cost=4200)
    assert "Deadline: 3 October, 18:00 MSK (48 hours)" in text
    assert "Role fee: 4 200 ₽" in text
    assert "https://studio.example/app/help" in text and "https://studio.example/app/?onboarding=1" in text
    assert "contact the studio" in text


def test_the_approval_letter_runs_to_the_studio_date(db, monkeypatch):
    text = _sent(db, monkeypatch, "Ветрова Ольга", cost=4200)
    assert "Deadline: 31 December 2026" in text and "Role fee: 4 200 ₽" in text
    assert "https://studio.example/app/help" in text


def test_no_cost_no_cost_line(db, monkeypatch):
    assert "Role fee" not in _sent(db, monkeypatch, "Ветрова Ольга")


def test_format_due():
    assert role_deadlines.format_due(datetime(2026, 10, 3, 15, 0, 0), kind="audition") == "3 October, 18:00 MSK (48 hours)"
    assert role_deadlines.format_due(datetime(2026, 12, 31, 20, 59, 59), kind="role") == "31 December 2026"


def test_the_narrator_letter_has_no_deadline(db, monkeypatch):
    # рассказчику срок не ставится (спека) — и в письме его быть не должно
    from app.services.shared_runtime import NARRATOR_DISPLAY_NAME
    texts = []
    monkeypatch.setattr(role_approval, "send_telegram_message",
                        lambda db, text, chat_ids=None, direct=False, **kw: texts.append(text) or 1)
    role_approval.notify_role_approved(db, book_id="b1", book_title="СВ3", role=NARRATOR_DISPLAY_NAME,
                                       actor_name="Ветрова Ольга")
    assert "Deadline:" not in texts[0] and "Role fee" not in texts[0]



def test_the_audition_letter_says_send_it_to_the_bot(db, monkeypatch):
    """С 06.10 пробу принимает и бот (пакет C2): приглашение называет оба пути."""
    text = _sent(db, monkeypatch, "Ветрова Ольга?")
    assert "send it here as an audio file or voice message (up to " in text
    assert "select the book and role in Upload" in text


def test_the_audition_letter_names_the_bot_limit_of_the_current_node(db, monkeypatch):
    """С 22.10 бот на своём узле и берёт файлы до 2 ГБ: письмо не должно гнать WAV на сайт
    старым «до 20 МБ». Облако — по-прежнему 20 МБ."""
    monkeypatch.setattr(settings, "telegram_api_base", "http://127.0.0.1:8092")
    assert "(up to 2 GB)" in _sent(db, monkeypatch, "Ветрова Ольга?")
    monkeypatch.setattr(settings, "telegram_api_base", "")
    assert "(up to 20 MB)" in _sent(db, monkeypatch, "Ветрова Ольга?")
