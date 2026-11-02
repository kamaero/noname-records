"""Благодарственное письмо участникам озвученной книги (план book-lifecycle, задача 4)."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import AudioFile, BookThanksSent, ScriptBook
from app.services import book_thanks, role_deadlines
from app.services.audio_uploads import derive_book_code
from app.v2 import lifecycle_api

NOW = datetime(2026, 10, 29, 12, 0, 0)
CHATS = {"Иванова Ольга": "111", "Петров Пётр": "222", "Сидоров Иван": "999", "Без Чата": ""}


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False)
    with maker() as db:
        db.add(ScriptBook(id="b1", title="Карта приозерья", display_title="Карта", source_filename="x.docx",
                          source_format="docx", total_chars=0, author_sheets_x1000=0, chapter_count=1,
                          has_chapters="true", status="processing", voiced_at=NOW))
        code = derive_book_code("Карта приозерья")
        for i, (actor, role, kind) in enumerate([
            ("Иванова Ольга", "Хдыбдяр", "take"), ("Иванова Ольга", "Тот-Кто", "take"),
            ("Иванова Ольга", "Хдыбдяр", "take"), ("Петров Пётр", "Рассказчик", "take"),
            ("Сидоров Иван", "Хозяин", "take"), ("Без Чата", "Страж", "take"),
            ("Петров Пётр", "Проба", "audition"),
        ]):
            db.add(AudioFile(id=f"a{i}", book_code=code, original_filename="f.wav", stored_key=f"k{i}",
                             mime_type="audio/wav", size_bytes=1, chapter="1", role=role, actor_name=actor,
                             canonical_filename=f"c{i}.wav", kind=kind))
        db.commit()
    monkeypatch.setattr(role_deadlines, "chat_for", lambda _db, actor: CHATS.get(actor, ""))
    monkeypatch.setattr(settings, "owner_telegram_id", "999")
    return maker


@pytest.fixture()
def db(factory):
    with factory() as session:
        yield session


def test_recipients_are_people_with_takes_merged_by_chat_without_the_owner(db):
    out = book_thanks.recipients(db, "b1")
    people = {p["chat"]: p for p in out["people"]}
    assert set(people) == {"111", "222"}  # владелец (999) исключён, проба не в счёт
    assert people["111"]["roles"] == ["Хдыбдяр", "Тот-Кто"]
    assert people["222"]["roles"] == ["Рассказчик"]
    assert people["111"]["sent_at"] == ""


def test_people_without_a_chat_are_listed_separately(db):
    out = book_thanks.recipients(db, "b1")
    assert out["no_chat"] == [{"actor": "Без Чата", "roles": ["Страж"]}]


def test_render_joins_roles_in_russian():
    t = "{name}, вы озвучили {roles}."
    assert book_thanks.render(t, name="Ольга", roles=["Хдыбдяр"]) == "Ольга, вы озвучили Хдыбдяр."
    assert "Хдыбдяр и Тот-Кто" in book_thanks.render(t, name="О", roles=["Хдыбдяр", "Тот-Кто"])
    assert "А, Б и В" in book_thanks.render(t, name="О", roles=["А", "Б", "В"])
    assert "Ольга" in book_thanks.render(book_thanks.DEFAULT_TEXT, name="Ольга", roles=["Хдыбдяр"])


def test_send_twice_sends_once(db):
    calls = []
    first = book_thanks.send(db, "b1", book_thanks.DEFAULT_TEXT, send_message=lambda c, t: calls.append((c, t)) or True,
                             now=NOW)
    assert sorted(first["sent"]) == ["Иванова Ольга", "Петров Пётр"] and not first["already"]
    assert db.query(BookThanksSent).count() == 2
    assert "Ольга" in dict(calls)["111"]
    calls.clear()
    second = book_thanks.send(db, "b1", book_thanks.DEFAULT_TEXT, send_message=lambda c, t: calls.append(c) or True)
    assert calls == [] and sorted(second["already"]) == ["Иванова Ольга", "Петров Пётр"] and not second["sent"]


def test_failed_send_is_reported_and_not_retried(db):
    out = book_thanks.send(db, "b1", "{name}", send_message=lambda c, t: False, now=NOW)
    assert sorted(out["failed"]) == ["Иванова Ольга", "Петров Пётр"]
    assert db.query(BookThanksSent).count() == 2  # отметка до отправки: падение не приводит к дублю


def test_thanks_refused_for_a_book_that_is_not_voiced(monkeypatch, factory):
    monkeypatch.setattr(lifecycle_api, "SessionLocal", factory)
    monkeypatch.setattr(lifecycle_api, "is_authenticated", lambda _r: True)
    monkeypatch.setattr(lifecycle_api, "has_any_role", lambda _r, allowed: True)
    with factory() as db:
        db.get(ScriptBook, "b1").voiced_at = None
        db.commit()
    resp = lifecycle_api.api_v2_book_thanks_send("b1", SimpleNamespace(), {"text": "x"})
    assert resp.status_code == 409
    assert lifecycle_api.api_v2_book_thanks("b1", SimpleNamespace()).status_code == 409
    with factory() as db:
        db.get(ScriptBook, "b1").voiced_at = NOW
        db.commit()
    out = lifecycle_api.api_v2_book_thanks("b1", SimpleNamespace())
    assert out["ok"] and out["text"] == book_thanks.DEFAULT_TEXT and len(out["people"]) == 2 and out["preview"]


def test_failed_send_shows_sent_at_but_not_delivered(db):
    book_thanks.send(db, "b1", "{name}", send_message=lambda c, t: False, now=NOW)
    people = book_thanks.recipients(db, "b1")["people"]
    assert all(p["sent_at"] and p["delivered"] is False for p in people)


def test_successful_send_marks_delivered(db):
    book_thanks.send(db, "b1", "{name}", send_message=lambda c, t: True, now=NOW)
    people = book_thanks.recipients(db, "b1")["people"]
    assert all(p["sent_at"] and p["delivered"] is True for p in people)


def test_unsent_people_are_not_delivered(db):
    assert all(p["delivered"] is False for p in book_thanks.recipients(db, "b1")["people"])


def test_get_payload_carries_the_salutation_name(monkeypatch, factory):
    monkeypatch.setattr(lifecycle_api, "SessionLocal", factory)
    monkeypatch.setattr(lifecycle_api, "is_authenticated", lambda _r: True)
    monkeypatch.setattr(lifecycle_api, "has_any_role", lambda _r, allowed: True)
    out = lifecycle_api.api_v2_book_thanks("b1", SimpleNamespace())
    assert {p["actor"]: p["name"] for p in out["people"]} == {"Иванова Ольга": "Ольга", "Петров Пётр": "Пётр"}
