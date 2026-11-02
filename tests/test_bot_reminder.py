"""Кнопка «⏰ Напомнить» в боте (решение владельца 05.10).

Массовое напоминание утверждённым дикторам о записи — умное: только по ролям, к которым
диктор ещё не приступил (сдано 0 глав). Кто начал или сдал — не получает. Одно письмо на
человека, со всеми его ненаписанными ролями; перед отправкой — сводка и образец.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AudioFile, AuditLog, BotBroadcast, Character, RoleDeadline, ScriptBook, ScriptChapter, TelegramAuthAccount,
    User, UserRole,
)
from app.config import settings
from app.services import bot_reminder, onboarding
from app.services.audio_uploads import derive_book_code
from app.services.bot_dialog import BROADCAST_BUTTON, REMIND_BUTTON, handle_update

NOW = datetime(2026, 10, 5, 9, 0, 0)


@pytest.fixture(autouse=True)
def _studio_settings(monkeypatch):
    monkeypatch.setattr(settings, "app_base_url", "https://studio.example")
    monkeypatch.setattr(settings, "studio_contact_name", "в студию")
DUE = datetime(2026, 12, 31, 20, 59, 59)
CHATS = {"Ветрова Ольга": "41", "Иванов Пётр": "42", "Сидорова Анна": "44"}


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(bot_reminder, "chat_for", lambda db, actor: CHATS.get(actor, ""))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        for bid, title in (("b1", "Карта Приозерья"), ("b2", "Сезон ветров. Книга 3")):
            session.add(ScriptBook(id=bid, title=title, display_title=title, source_filename="x", source_format="docx",
                                   total_chars=0, author_sheets_x1000=0, chapter_count=3, has_chapters="true",
                                   status="processing"))
            for n in (1, 2, 3):
                session.add(ScriptChapter(id=f"{bid}-{n}", book_id=bid, chapter_index=n, chapter_title=f"Глава {n}"))
        session.add(User(id="admin", login="admin", password_hash="x", display_name="Примеряфеицшпчб Примерщюагоэсгв", is_active="true"))
        session.add(UserRole(user_id="admin", role="admin"))
        session.add(TelegramAuthAccount(telegram_user_id="900000100", role="admin", access_scope="full",
                                        display_name="Примеряфеицшпчб Примерщюагоэсгв", is_active="true", user_id="admin"))
        roles = [
            # (id, книга, роль, актёр, главы)
            ("c1", "b1", "Убув", "Ветрова Ольга", "1,2"),
            ("c2", "b1", "Хоркон", "Ветрова Ольга", "1,2,3"),      # начата — 1 глава сдана
            ("c3", "b1", "Фибэл", "Иванов Пётр", "2"),            # сдана целиком — срок закрыт
            ("c4", "b1", "Химес", "Без Телеграма", "3"),
            ("c5", "b2", "Астрид", "Сидорова Анна", "1,2,3"),
            ("c6", "b1", "Фэдывнютд", "Сидорова Анна", "1"),
        ]
        for cid, bid, name, actor, chapters in roles:
            session.add(Character(id=cid, book_id=bid, name=name, actor_name=actor, appears_in=chapters))
            closed = NOW if cid == "c3" else None
            session.add(RoleDeadline(character_id=cid, book_id=bid, actor_name=actor, kind="role", due_at=DUE,
                                     created_at=NOW, closed_at=closed, close_reason="done" if closed else None))
        session.add(RoleDeadline(character_id="c1", book_id="b1", actor_name="Иванов Пётр", kind="audition",
                                 due_at=NOW, created_at=NOW))
        session.add(AudioFile(book_code=derive_book_code("Карта Приозерья"), original_filename="k.wav",
                              stored_key="k/1", mime_type="audio/wav", size_bytes=1, chapter="Глава 1", role="Хоркон",
                              actor_name="Ветрова Ольга", kind="take", uploaded_at=NOW))
        session.commit()
        yield session


def _msg(text, tid="900000100"):
    return {"update_id": 1, "message": {"message_id": 1, "from": {"id": int(tid)},
                                        "chat": {"id": int(tid), "type": "private"}, "text": text}}


def _press(data, tid="900000100"):
    return {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": int(tid)}, "data": data,
                                               "message": {"message_id": 9, "chat": {"id": int(tid), "type": "private"}}}}


def _buttons(out):
    return [b for o in out for row in o.payload.get("reply_markup", {}).get("inline_keyboard", []) for b in row]


def _texts(out):
    return [o.payload.get("text", "") for o in out if o.method == "sendMessage"]


def _pick(out, label):
    return next(b["callback_data"] for b in _buttons(out) if label in b["text"])


# ---------- кого и о чём ----------

def test_only_roles_not_started_are_reminded_one_letter_per_person(db):
    found = {t.actor: t for t in bot_reminder.targets(db, book_id="b1")}

    assert set(found) == {"Ветрова Ольга", "Без Телеграма", "Сидорова Анна"}
    assert [r.role for r in found["Ветрова Ольга"].roles] == ["Убув"]     # Хоркон начат, Фибэл у другого
    assert found["Без Телеграма"].chat == ""


def test_all_books_put_every_role_of_a_person_into_one_letter(db):
    anna = next(t for t in bot_reminder.targets(db, book_id="") if t.actor == "Сидорова Анна")
    assert sorted((r.book_title, r.role) for r in anna.roles) == [
        ("Карта Приозерья", "Фэдывнютд"), ("Сезон ветров. Книга 3", "Астрид"),
    ]


def test_the_letter_word_for_word(db):
    anna = next(t for t in bot_reminder.targets(db, book_id="") if t.actor == "Сидорова Анна")
    assert bot_reminder.reminder_text(anna) == (
        "🎙 Noname Records — напоминание о записи\n"
        "\n"
        "«Карта Приозерья»:\n"
        "• «Фэдывнютд» — 1 глава\n"
        "«Сезон ветров. Книга 3»:\n"
        "• «Астрид» — 3 главы\n"
        "\n"
        "Срок — до 31 декабря 2026.\n"
        "Записи загружайте на studio.example, раздел «Загрузка». По книге, которая сейчас в работе студии, "
        "дубли можно прислать и файлом в бот. Если нужна помощь или больше "
        "времени — напишите в студию."
    )


# ---------- кнопка в боте ----------

def test_the_admin_keyboard_has_both_buttons(db):
    out = handle_update(db, _msg("/start"), now=NOW)
    # с 05.10 вторым рядом — «🏢 Студия» и «📋 Мой статус»
    assert out[0].payload["reply_markup"]["keyboard"][0] == [{"text": BROADCAST_BUTTON}, {"text": REMIND_BUTTON}]


def test_the_flow_asks_the_book_shows_a_preview_and_sends_once(db):
    out = handle_update(db, _msg(REMIND_BUTTON), now=NOW)
    assert "Все книги" in [b["text"] for b in _buttons(out)]

    out = handle_update(db, _press(_pick(out, "Карта")), now=NOW)
    text = "\n".join(_texts(out))
    assert "Напоминание уйдёт 2 дикторам" in text            # Ветрова и Сидорова
    assert "Без Telegram: Без Телеграма" in text
    assert "«Убув» — 2 главы" in text or "«Фэдывнютд» — 1 глава" in text   # образец письма

    send = _pick(out, "Отправить")
    first = handle_update(db, _press(send), now=NOW)
    again = handle_update(db, _press(send), now=NOW)
    assert [o.method for o in first].count("reminder") == 1
    assert "reminder" not in [o.method for o in again]


def test_nobody_to_remind_says_so(db):
    db.query(RoleDeadline).delete()
    db.commit()
    out = handle_update(db, _msg(REMIND_BUTTON), now=NOW)
    out = handle_update(db, _press(_pick(out, "Все книги")), now=NOW)
    assert "напоминать некому" in "\n".join(_texts(out))
    assert "Отправить" not in [b["text"] for b in _buttons(out)]


def test_a_dictor_cannot_press_the_admin_buttons(db):
    out = handle_update(db, _msg(REMIND_BUTTON), now=NOW)
    data = _pick(out, "Все книги")
    assert handle_update(db, _press(data, tid="41"), now=NOW)[1:] == []


# ---------- рассылка ----------

def test_deliver_sends_each_person_their_own_letter_and_logs_it(db):
    out = handle_update(db, _msg(REMIND_BUTTON), now=NOW)
    out = handle_update(db, _press(_pick(out, "Все книги")), now=NOW)
    sent_out = handle_update(db, _press(_pick(out, "Отправить")), now=NOW)
    bc_id = next(o.payload["id"] for o in sent_out if o.method == "reminder")
    sent = []

    summary = bot_reminder.deliver(db, bc_id, send=lambda chat, text: sent.append((chat, text)) or True, now=NOW)

    assert sorted(chat for chat, _ in sent) == ["41", "44"]
    assert "«Убув»" in dict(sent)["41"] and "«Хоркон»" not in dict(sent)["41"]
    assert "Ушло: 2" in summary and "Без Telegram: Без Телеграма" in summary
    assert db.query(AuditLog).filter_by(action="bot_reminder").count() == 1
    assert db.get(BotBroadcast, bc_id).sent_count == 2

