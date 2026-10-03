"""Проход по срокам: сданное закрывается, рекаст, напоминания по разу, утренняя сводка."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import AudioFile, Character, RoleDeadline, ScriptBook, ScriptChapter, TelegramAuthAccount, User, UserRole
from app.services import role_deadlines
from app.services.audio_uploads import derive_book_code

NOW = datetime(2026, 10, 1, 12, 0, 0)  # 15:00 МСК


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", "900000100")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Крылья", display_title="Крылья", source_filename="x.docx",
                               source_format="docx", total_chars=0, author_sheets_x1000=0, chapter_count=2,
                               has_chapters="true", status="processing"))
        session.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=1, chapter_title="Глава 1"))
        session.add(ScriptChapter(id="ch2", book_id="b1", chapter_index=2, chapter_title="Глава 2"))
        session.add(Character(id="c1", book_id="b1", name="Куйбу", aliases="", character_color="", appears_in="1,2",
                              actor_name="Ветрова Ольга"))
        session.add(User(id="u1", login="olga", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
        session.add(UserRole(user_id="u1", role="dictor"))
        session.add(TelegramAuthAccount(telegram_user_id="42", role="dictor", access_scope="full",
                                        display_name="Ветрова Ольга", is_active="true", user_id="u1"))
        session.commit()
        yield session


def _deadline(db, kind="role", due=NOW + timedelta(days=30), actor="Ветрова Ольга", created=NOW - timedelta(days=1)):
    row = RoleDeadline(character_id="c1", book_id="b1", actor_name=actor, kind=kind, due_at=due, created_at=created)
    db.add(row)
    db.commit()
    return row


def _file(db, chapter, kind="take", when=NOW):
    db.add(AudioFile(book_code=derive_book_code("Крылья"), original_filename="t.wav", stored_key=f"k/{chapter}{kind}",
                     mime_type="audio/wav", size_bytes=1, chapter=chapter, role="Куйбу", actor_name="Ветрова Ольга",
                     kind=kind, canonical_filename=f"{chapter}{kind}.wav", uploaded_at=when))
    db.commit()


def _run(db, now=NOW):
    sent = []
    stats = role_deadlines.run_pass(db, now=now, send=lambda chat, text: sent.append((chat, text)) or True)
    db.commit()
    return sent, stats


def test_a_role_closes_only_when_every_chapter_has_a_take(db):
    row = _deadline(db)
    _file(db, "Глава 1")
    _run(db)
    assert row.closed_at is None
    _file(db, "Глава 2")
    _run(db)
    assert row.close_reason == "done"


def test_an_audition_closes_when_the_audition_is_uploaded(db):
    row = _deadline(db, kind="audition", due=NOW + timedelta(hours=40))
    _file(db, "Глава 1", kind="audition")
    _run(db)
    assert row.close_reason == "done"


def test_a_recast_letter_goes_once(db):
    db.get(Character, "c1").actor_name = "Иванов Пётр"
    row = _deadline(db)
    row.closed_at, row.close_reason = NOW - timedelta(hours=1), "replaced"
    db.commit()
    sent, _ = _run(db)
    assert [chat for chat, _ in sent] == ["42"] and "рекаст" in sent[0][1] and "Куйбу" in sent[0][1]
    again, _ = _run(db)
    assert again == []


def test_reminders_go_once_before_and_once_after(db):
    _deadline(db, kind="audition", due=NOW + timedelta(hours=20))
    sent, _ = _run(db)
    assert len(sent) == 1 and "Напоминаем" in sent[0][1]
    assert _run(db)[0] == []
    late, _ = _run(db, now=NOW + timedelta(hours=21))
    assert len(late) == 1 and "Срок" in late[0][1] and "вышел" in late[0][1]
    assert _run(db, now=NOW + timedelta(hours=30))[0] == []


def test_the_role_reminder_tells_the_progress(db):
    _deadline(db, due=NOW + timedelta(hours=10))
    _file(db, "Глава 1")
    sent, _ = _run(db)
    assert "Сдано 1 из 2 глав" in sent[0][1]


def test_the_owner_digest_goes_at_ten_moscow_once_and_never_empty(db):
    at_ten = datetime(2026, 10, 2, 7, 5, 0)  # 10:05 МСК
    assert [c for c, _ in _run(db, now=at_ten)[0]] == []  # сказать нечего — сводки нет
    _deadline(db, due=datetime(2026, 10, 1, 20, 0, 0), created=datetime(2026, 9, 1))  # просрочено
    sent, _ = _run(db, now=at_ten)
    digest = [text for chat, text in sent if chat == "900000100"]
    assert len(digest) == 1 and "Просрочено" in digest[0] and "Ветрова Ольга" in digest[0]
    assert [t for c, t in _run(db, now=at_ten + timedelta(minutes=20))[0] if c == "900000100"] == []


def test_no_digest_outside_ten(db):
    _deadline(db, due=datetime(2026, 10, 1, 20, 0, 0), created=datetime(2026, 9, 1))
    sent, _ = _run(db, now=datetime(2026, 10, 2, 9, 0, 0))  # 12:00 МСК
    assert [c for c, _ in sent if c == "900000100"] == []


# --- ревью 01.10: ошибочные письма о рекасте и повторы ---

def _recast_row(db, closed_at, actor="Ветрова Ольга"):
    row = _deadline(db, actor=actor)
    row.closed_at, row.close_reason = closed_at, "replaced"
    db.commit()
    return row


def test_a_recast_letter_waits_half_an_hour(db):
    db.get(Character, "c1").actor_name = "Иванов Пётр"
    db.commit()
    _recast_row(db, NOW - timedelta(minutes=10))
    assert _run(db)[0] == []
    assert len(_run(db, now=NOW + timedelta(minutes=25))[0]) == 1


def test_no_recast_letter_when_the_person_is_back_on_the_role(db):
    # опечатка в касте и исправление назад: Ольга снова на роли — письма нет
    row = _recast_row(db, NOW - timedelta(hours=2))
    sent, _ = _run(db)
    assert sent == [] and row.recast_notified_at is not None


def test_letters_go_only_to_an_unambiguous_person(db):
    db.add(User(id="u2", login="olga2", password_hash="x", display_name="Ольга Сидорова", is_active="true"))
    db.add(TelegramAuthAccount(telegram_user_id="43", role="dictor", access_scope="full",
                               display_name="Ольга Сидорова", is_active="true", user_id="u2"))
    db.get(Character, "c1").actor_name = "Иванов Пётр"
    db.commit()
    _recast_row(db, NOW - timedelta(hours=2), actor="Ольга")
    assert _run(db)[0] == []


def test_marks_are_saved_before_each_letter(db):
    _deadline(db, kind="audition", due=NOW + timedelta(hours=20))

    def boom(chat, text):
        raise RuntimeError("telegram down")
    with pytest.raises(RuntimeError):
        role_deadlines.run_pass(db, now=NOW, send=boom)
    db.rollback()
    assert db.query(RoleDeadline).one().reminded_before_at is not None


def test_a_role_with_untitled_chapters_counts_them_by_number(db):
    db.get(ScriptChapter, "ch2").chapter_title = ""
    db.commit()
    _file(db, "Глава 1")
    assert role_deadlines.role_progress(db, db.get(Character, "c1"), "Ветрова Ольга") == (1, 2)
    _file(db, "Глава 2")
    assert role_deadlines.role_progress(db, db.get(Character, "c1"), "Ветрова Ольга") == (2, 2)
