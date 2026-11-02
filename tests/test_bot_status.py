"""Команды боту /help и /status (05.10).

/help — порядок работы студии, тот же текст, что у кнопки «ℹ️ Как мы работаем».
/status — сводка диктору: утверждённые роли с прогрессом и сроком, пробы, отказы, учётка.
Чужое и двусмысленное не показывается: имя в касте — «моё», только если указывает ровно
на меня (`chat_for`, как у писем).
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AudioFile, AuditionRejection, Character, RoleDeadline, ScriptBook, ScriptChapter, TelegramAuthAccount, User,
    UserRole,
)
from app.config import settings
from app.services import bot_status, onboarding
from app.services.audio_uploads import derive_book_code
from app.services.bot_dialog import handle_update
from app.services.login_identity import TELEGRAM_PASSWORD_HASH

NOW = datetime(2026, 10, 5, 9, 0, 0)
DUE = datetime(2026, 12, 31, 20, 59, 59)
# «Ольга» — двусмысленное имя: не указывает ни на кого
CHATS = {"Ветрова Ольга": "41", "Иванов Пётр": "42"}
KP = "Карта Приозерья"


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(bot_status, "chat_for", lambda db, actor: CHATS.get(actor, ""))
    monkeypatch.setattr(settings, "app_base_url", "http://localhost:8080")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title=KP, display_title=KP, source_filename="x", source_format="docx",
                               total_chars=0, author_sheets_x1000=0, chapter_count=3, has_chapters="true",
                               status="processing"))
        for n in (1, 2, 3):
            session.add(ScriptChapter(id=f"b1-{n}", book_id="b1", chapter_index=n, chapter_title=f"Глава {n}"))
        session.add(User(id="u41", login="tg_41", password_hash=TELEGRAM_PASSWORD_HASH, display_name="Ветрова Ольга",
                         is_active="true"))
        session.add(UserRole(user_id="u41", role="dictor"))
        session.add(TelegramAuthAccount(telegram_user_id="41", role="dictor", access_scope="full",
                                        display_name="Ветрова Ольга", is_active="true", user_id="u41"))
        cast = [
            ("c1", "Убув", "Ветрова Ольга", "1,2"),        # утверждена, сдана 1 из 2
            ("c2", "Хоркон", "Ветрова Ольга", "1"),        # сдана целиком
            ("c3", "Фэдывнютд", "Ветрова Ольга?", "3"),    # позвали на пробу
            ("c4", "Химес", "Иванов Пётр", "3"),           # чужая
            ("c5", "Фибэл", "Ольга", "2"),                 # двусмысленное имя
            ("c6", "Ге'Коон", "Иванов Пётр", "1"),         # пробовалась, утвердили другого
            ("c7", "Хдыбдяр", "", "1"),                    # проба загружена, решения нет
        ]
        for cid, name, actor, chapters in cast:
            session.add(Character(id=cid, book_id="b1", name=name, actor_name=actor, appears_in=chapters))
        session.add(RoleDeadline(character_id="c1", book_id="b1", actor_name="Ветрова Ольга", kind="role",
                                 due_at=DUE, created_at=NOW))
        session.add(RoleDeadline(character_id="c3", book_id="b1", actor_name="Ветрова Ольга", kind="audition",
                                 due_at=NOW + timedelta(hours=30), created_at=NOW))
        code = derive_book_code(KP)
        for i, (chapter, role, kind) in enumerate([("Глава 1", "Убув", "take"), ("Глава 1", "Хоркон", "take"),
                                                   ("", "ге'коон", "audition"), ("", "Хдыбдяр", "audition")]):
            session.add(AudioFile(book_code=code, original_filename=f"{i}.wav", stored_key=f"k/{i}",
                                  mime_type="audio/wav", size_bytes=1, chapter=chapter, role=role,
                                  actor_name="Ветрова Ольга", kind=kind, uploaded_at=NOW))
        session.add(AuditionRejection(book_id="b1", book_code=code, role="Ге'Коон", actor_name="Ветрова Ольга",
                                      due_at=NOW, status="sent", sent_at=NOW))
        session.commit()
        yield session


def _msg(text, tid="41"):
    return {"update_id": 1, "message": {"message_id": 1, "from": {"id": int(tid)},
                                        "chat": {"id": int(tid), "type": "private"}, "text": text}}


def _status(db, tid="41"):
    out = handle_update(db, _msg("/status", tid), now=NOW)
    assert out[0].payload["parse_mode"] == "HTML"
    return out[0].payload["text"]


def test_help_shows_the_order_of_work(db):
    for command in ("/help", "/help@nonamerecords_bot"):
        text = handle_update(db, _msg(command), now=NOW)[0].payload["text"]
        assert "Как мы работаем — по шагам" in text and "Проба — только по приглашению" in text


def test_strangers_get_neither_help_nor_status(db):
    for command in ("/help", "/status"):
        assert "Доступ к сайту пока не открыт" in handle_update(db, _msg(command, "999"), now=NOW)[0].payload["text"]


def test_status_lists_approved_roles_with_progress_and_deadline(db):
    text = _status(db)
    assert "«Убув» — «Карта Приозерья»: сдано 1 из 2 глав, срок до 31 декабря 2026" in text
    assert "«Хоркон» — «Карта Приозерья»: сдано 1 из 1 главы ✓" in text


def test_status_lists_auditions_invited_and_uploaded(db):
    text = _status(db)
    assert "«Фэдывнютд» — «Карта Приозерья»: ждём вашу пробу до 6 октября, 18:00 МСК" in text
    assert "«Хдыбдяр» — «Карта Приозерья»: проба загружена, ждёт решения" in text


def test_a_sent_rejection_is_shown_once_and_not_as_a_pending_audition(db):
    text = _status(db)
    assert "Отказы" in text and "«Ге'Коон» — «Карта Приозерья»: выбран другой исполнитель" in text
    assert "Ге'Коон» — «Карта Приозерья»: проба загружена" not in text


def test_a_pending_rejection_is_not_announced_nor_called_undecided(db):
    """Отказ ещё может отмениться — сводка его не объявляет. Но и «ждёт решения» не пишет:
    роль уже отдана другому."""
    db.query(AuditionRejection).update({"status": "pending"})
    db.commit()
    text = _status(db)
    assert "Отказы" not in text and "Ге'Коон" not in text


def test_foreign_and_ambiguous_roles_are_not_shown(db):
    text = _status(db)
    assert "Химес" not in text and "Фибэл" not in text


def test_status_explains_the_account_and_how_to_sign_in(db):
    text = _status(db)
    assert "логин <code>tg_41</code>" in text and "Пароля нет" in text and "localhost:8080/app/login" in text


def test_status_with_nothing_yet_says_so(db):
    db.add(User(id="u42", login="tg_42", password_hash="hash", display_name="Иванов Пётр", is_active="true"))
    db.add(UserRole(user_id="u42", role="dictor"))
    db.add(TelegramAuthAccount(telegram_user_id="43", role="dictor", access_scope="full", display_name="Новичок",
                               is_active="true", user_id="u42"))
    db.commit()
    text = _status(db, "43")
    assert "Пока нет." in text and "Приглашений на пробу сейчас нет." in text and "Пароль выдан" in text


def test_a_long_status_fits_one_telegram_message(db):
    for i in range(300):
        db.add(Character(id=f"x{i}", book_id="b1", name=f"Роль номер {i}", actor_name="Ветрова Ольга", appears_in="1"))
    db.commit()
    text = _status(db)
    assert len(text) <= 4096 and "полностью — на сайте" in text


def test_a_taken_audition_says_so(db):
    """Пакет B: после «✅ Беру» — «проба взята, ждём до …», а не «ждём вашу пробу»."""
    db.query(RoleDeadline).filter_by(character_id="c3").update({"accepted_at": NOW})
    db.commit()
    assert "«Фэдывнютд» — «Карта Приозерья»: проба взята, ждём до 6 октября, 18:00 МСК" in _status(db)
