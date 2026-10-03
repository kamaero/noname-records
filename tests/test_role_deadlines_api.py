"""Сроки на экранах и в руках админа: продлить, закрыть; срок в карточке диктора."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import dictors as dictors_api
from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import (
    Character, DictorAssignment, RoleDeadline, ScriptBook, ScriptChapter, TelegramAuthAccount, User, UserRole,
)
from app.services import role_deadlines
from app.time_utils import utcnow_naive


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(dictors_api, "SessionLocal", factory)
    with factory() as db:
        db.add(ScriptBook(id="b1", title="Крылья", display_title="Крылья", source_filename="x", source_format="docx",
                          total_chars=0, author_sheets_x1000=0, chapter_count=2, has_chapters="true", status="processing"))
        db.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=1, chapter_title="Глава 1"))
        db.add(ScriptChapter(id="ch2", book_id="b1", chapter_index=2, chapter_title="Глава 2"))
        db.add(Character(id="c1", book_id="b1", name="Куйбу", aliases="", character_color="", appears_in="1,2",
                         actor_name="Ветрова Ольга"))
        db.add(User(id="u1", login="olga", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
        db.add(UserRole(user_id="u1", role="dictor"))
        db.add(TelegramAuthAccount(telegram_user_id="42", role="dictor", access_scope="full",
                                   display_name="Ветрова Ольга", is_active="true", user_id="u1"))
        db.add(DictorAssignment(user_id="u1", book_id="b1", character_id="c1", role_name="Куйбу"))
        db.add(RoleDeadline(id="d1", character_id="c1", book_id="b1", actor_name="Ветрова Ольга", kind="role",
                            due_at=utcnow_naive() - timedelta(days=2), created_at=utcnow_naive() - timedelta(days=30),
                            reminded_overdue_at=utcnow_naive()))
        db.commit()
    return factory


@pytest.fixture()
def letters(monkeypatch):
    sent = []
    monkeypatch.setattr(dictors_api, "send_telegram_message",
                        lambda db, text, chat_ids=None, direct=False, **kw: sent.append((chat_ids, text)) or 1)
    return sent


def _client(roles):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "x", "sub": "x", "roles": roles, "display_name": "Тимур"}))
    return client


def test_the_card_shows_the_deadline_and_progress(factory):
    card = _client(["author"]).get("/api/dictors/u1").json()
    deadline = card["roles"][0]["deadline"]
    assert deadline["id"] == "d1" and deadline["kind"] == "role" and deadline["overdue"] is True
    assert (deadline["done"], deadline["total"]) == (0, 2)


@pytest.mark.parametrize("roles", [["author"], ["agent"], ["dictor"]])
def test_only_the_admin_extends_or_closes(factory, roles):
    api = _client(roles)
    assert api.post("/api/deadlines/d1/extend", json={"days": 3}).status_code == 403
    assert api.post("/api/deadlines/d1/close").status_code == 403


def test_extending_moves_the_date_resets_reminders_and_tells_the_dictor(factory, letters):
    before = utcnow_naive()
    r = _client(["admin"]).post("/api/deadlines/d1/extend", json={"days": 3})
    assert r.status_code == 200
    with factory() as db:
        row = db.get(RoleDeadline, "d1")
        assert row.due_at > before + timedelta(days=2, hours=23)
        assert row.reminded_overdue_at is None and row.reminded_before_at is None
        assert row.extended_by == "Тимур"
    assert letters and letters[0][0] == ["42"] and "продлён" in letters[0][1]


def test_extending_to_a_date_uses_the_end_of_that_moscow_day(factory, letters):
    _client(["admin"]).post("/api/deadlines/d1/extend", json={"date": "2027-01-15"})
    with factory() as db:
        assert db.get(RoleDeadline, "d1").due_at == datetime(2027, 1, 15, 20, 59, 59)


def test_closing_by_hand(factory):
    assert _client(["admin"]).post("/api/deadlines/d1/close").status_code == 200
    with factory() as db:
        assert db.get(RoleDeadline, "d1").close_reason == "manual"


def test_bad_extension_is_refused(factory):
    api = _client(["admin"])
    assert api.post("/api/deadlines/d1/extend", json={"days": 0}).status_code == 400
    assert api.post("/api/deadlines/d1/extend", json={"date": "вчера"}).status_code == 400
    assert api.post("/api/deadlines/nope/extend", json={"days": 1}).status_code == 404


def test_book_views_for_the_cast(factory):
    with factory() as db:
        views = role_deadlines.book_views(db, "b1")
    assert views["c1"]["id"] == "d1"


def test_the_cast_row_carries_the_deadline(factory):
    # Маршрут каста живёт в реестре и держит глобальную фабрику сессий — перенаправляем её
    # на тестовую базу, как в tests/test_agent_cast_assign.py.
    from app.db import SessionLocal, engine as app_engine
    SessionLocal.configure(bind=factory.kw["bind"])
    try:
        r = _client(["admin"]).get("/api/budget/b1")
    finally:
        SessionLocal.configure(bind=app_engine)
    rows = {row["character_id"]: row for row in r.json()["characters"]}
    assert rows["c1"]["deadline"]["id"] == "d1"
