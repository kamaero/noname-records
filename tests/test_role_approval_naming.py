"""Письма об утверждении и о пробе учат называть файлы — пример собирает та же функция,
что даёт имя при сохранении, поэтому транслитерация живёт в одном месте."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter, TelegramAuthAccount, User, UserRole
from app.services import role_approval

EXAMPLE = "TO_Ch13_Mirael_MorkenMelvis.wav"


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "https://noname.example")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Тайны Острова", display_title="Тайны Острова",
                               source_filename="x.docx", source_format="docx", total_chars=0,
                               author_sheets_x1000=0, chapter_count=2, has_chapters="true", status="processing"))
        session.add(ScriptChapter(id="ch13", book_id="b1", chapter_index=13, chapter_title="Глава 13"))
        session.add(ScriptChapter(id="ch20", book_id="b1", chapter_index=20, chapter_title="Глава 20"))
        session.add(Character(id="c1", book_id="b1", name="Мираэль", aliases="", character_color="",
                              appears_in="20,13"))
        session.add(Character(id="c2", book_id="b1", name="Безглавый", aliases="", character_color=""))
        session.add(User(id="u1", login="g", password_hash="x", display_name="Моркен Мелвис", is_active="true"))
        session.add(UserRole(user_id="u1", role="dictor"))
        session.add(TelegramAuthAccount(telegram_user_id="42", role="dictor", access_scope="full",
                                        display_name="Моркен Мелвис", is_active="true", user_id="u1"))
        session.commit()
        yield session


def _letter(db, monkeypatch, actor, role="Мираэль"):
    texts = []
    monkeypatch.setattr(role_approval, "send_telegram_message",
                        lambda db, text, chat_ids=None, direct=False, **kw: texts.append(text) or 1)
    monkeypatch.setattr(role_approval, "utcnow_naive", lambda: datetime(2026, 10, 1, 15, 0, 0), raising=False)
    role_approval.notify_role_approved(db, book_id="b1", book_title="Тайны Острова", role=role, actor_name=actor)
    return texts[0]


def test_approval_letter_teaches_file_names(db, monkeypatch):
    text = _letter(db, monkeypatch, "Моркен Мелвис")
    assert EXAMPLE in text
    assert "TO — книга («Тайны Острова»)" in text
    assert "один файл — одна глава" in text
    assert "Ch13 — глава 13 (Chapter)" in text
    assert "Mirael — роль латиницей" in text
    assert "MorkenMelvis — фамилия и имя диктора латиницей, без пробела" in text
    assert "Записывайте всю роль по главам." not in text


def test_audition_letter_teaches_file_names(db, monkeypatch):
    text = _letter(db, monkeypatch, "Моркен Мелвис?")
    assert EXAMPLE in text
    assert "TO — книга («Тайны Острова»)" in text
    assert "Пробу назовите так же, а роль, когда вас утвердят, записывайте по главам: один файл — одна глава." in text


def test_role_without_chapters_uses_ch01(db, monkeypatch):
    text = _letter(db, monkeypatch, "Моркен Мелвис", role="Безглавый")
    assert "TO_Ch01_" in text
    assert "Ch01 — глава 1 (Chapter)" in text


def test_prologue_as_first_chapter_does_not_break_the_letter(db, monkeypatch):
    """У «Пролога» нет токена главы — раньше разбор имени по «_» падал IndexError."""
    db.add(ScriptChapter(id="ch0", book_id="b1", chapter_index=0, chapter_title="Пролог"))
    db.get(Character, "c1").appears_in = "0,20,13"
    db.commit()
    text = _letter(db, monkeypatch, "Моркен Мелвис")
    assert EXAMPLE in text and "Ch13 — глава 13 (Chapter)" in text


def test_a_failing_naming_block_never_breaks_the_letter(db, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(role_approval, "naming_block", boom)
    text = _letter(db, monkeypatch, "Моркен Мелвис")
    assert "Мираэль" in text and "Как называть файлы" not in text
    assert "Записывайте всю роль по главам." in text
