"""Общая фикстура «Сметы»: две книги, дикторы с разным написанием, Рассказчик, дубли персонажа."""
from contextlib import contextmanager
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AudioFile, BookBudget, BotContact, Character, CharacterBudgetSnapshot, ScriptBook, ScriptChapter,
    TelegramAuthAccount, User, UserRole,
)
from app.services import payouts
from app.services.audio_uploads import derive_book_code

NOW = datetime(2026, 10, 6, 9, 0, 0)
KP, SV = "Карта Приозерья", "Сезон ветров. Книга 2"


def _book(session, bid, title, chapters):
    session.add(ScriptBook(id=bid, title=title, display_title=title, source_filename="x", source_format="docx",
                           total_chars=0, author_sheets_x1000=0, chapter_count=len(chapters), has_chapters="true",
                           status="processing"))
    for n, label in enumerate(chapters, start=1):
        session.add(ScriptChapter(id=f"{bid}-{n}", book_id=bid, chapter_index=n, chapter_title=label))


def build(session):
    _book(session, "b1", KP, ["Глава 1", "", "Эпилог"])    # у второй главы нет названия — метка «Глава 2»
    _book(session, "b2", SV, ["Глава 1"])
    for uid, tid, name, role in (("u11", "11", "Примеряфеицшпчб Примерщюагоэсгв", "admin"), ("u41", "41", "Ветрова Ольга", "dictor")):
        session.add(User(id=uid, login=f"tg_{tid}", password_hash="x", display_name=name, is_active="true"))
        session.add(UserRole(user_id=uid, role=role))
        session.add(TelegramAuthAccount(telegram_user_id=tid, role=role, access_scope="full", display_name=name,
                                        is_active="true", user_id=uid))
    session.add(BotContact(telegram_user_id="41", tg_name="Ольга", username="olga", first_seen_at=NOW, last_seen_at=NOW))
    cast = [
        ("c1", "b1", "m1", "Убув", "Ветрова Ольга", "1,2,3"),
        ("c1b", "b1", "m1", "Убув ", "", "1"),               # дубль того же персонажа — свёртка сметы
        ("c2", "b1", "m2", "Хоркон", "Ветрова Ольга", "1,2"),
        ("c3", "b1", "m3", "Дракон", "Иванов Пётр?", "1"),    # не утверждён — в «Смете» нет
        ("c4", "b1", "m4", "Без суммы", "Иванов Пётр", "1"),  # снапшота нет
        ("cn", "b1", "m5", "Рассказчик", "Примервсммчпфиб Примерубщркгюд", ""),
        ("c5", "b2", "m6", "Ныкфо", "Ольга Ветрова", "1"),    # то же лицо, иначе написано
    ]
    for cid, bid, cmap, name, actor, chapters in cast:
        session.add(Character(id=cid, book_id=bid, char_map_id=cmap, name=name, actor_name=actor, appears_in=chapters))
    for cid, bid, rub in (("c1", "b1", 12000), ("c1b", "b1", 500), ("c2", "b1", 5000), ("cn", "b1", 999999),
                          ("c5", "b2", 3000)):
        session.add(CharacterBudgetSnapshot(book_id=bid, character_id=cid, lines_count=3, approx_seconds=60,
                                            total_rub=rub))
    session.add(BookBudget(book_id="b1", narrator_actor_name="Примервсммчпфиб Примерубщркгюд", narrative_cost_rub=100000))
    code = derive_book_code(KP)
    takes = [("Убув", label, "Ветрова Ольга") for label in ("Глава 1", "Глава 2", "Эпилог")]
    takes += [("Хоркон", "Глава 1", "Ветрова Ольга")]
    takes += [("Рассказчик", label, "Примервсммчпфиб Примерубщркгюд") for label in ("Глава 1", "Глава 2", "Эпилог")]
    for i, (role, chapter, actor) in enumerate(takes):
        session.add(AudioFile(book_code=code, original_filename=f"{i}.wav", stored_key=f"k/{i}", mime_type="audio/wav",
                              size_bytes=1, chapter=chapter, role=role, actor_name=actor, kind="take", uploaded_at=NOW))
    session.commit()


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(payouts.settings, "owner_telegram_id", "11")
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as session:
        build(session)
        session.factory = factory
        yield session


@contextmanager
def same_session(session):
    yield session
