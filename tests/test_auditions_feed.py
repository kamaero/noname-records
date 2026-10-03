"""Раздел «Пробы»: лента проб всех книг, свежие сверху (02.10)."""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auditions_feed as feed_api
from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import AudioFile, AuditionReaction, Character, ScriptBook
from app.services.audio_uploads import derive_book_code


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(feed_api, "SessionLocal", factory)
    with factory() as db:
        for bid, title in (("b1", "Крылья Полумрака"), ("b2", "Сказки волшебников. Книга 3")):
            db.add(ScriptBook(id=bid, title=title, display_title=title, source_filename="x", source_format="docx",
                              total_chars=0, author_sheets_x1000=0, chapter_count=1, has_chapters="true",
                              status="processing"))
        db.add(Character(id="c1", book_id="b1", name="Маура", aliases="", character_color="", actor_name="Кравченко Алёна"))
        db.add(Character(id="c2", book_id="b2", name="Астрид", aliases="", character_color="", actor_name=""))
        for aid, book, role, actor, when in (
            ("a1", "Крылья Полумрака", "Маура", "Кравченко Алёна", datetime(2026, 10, 2, 9)),
            ("a2", "Крылья Полумрака", "Маура", "Ветрова Ольга", datetime(2026, 10, 1, 9)),
            ("a3", "Сказки волшебников. Книга 3", "Астрид", "Ветрова Ольга", datetime(2026, 10, 2, 12)),
        ):
            db.add(AudioFile(id=aid, book_code=derive_book_code(book), original_filename="p.wav", stored_key=f"k/{aid}",
                             mime_type="audio/wav", size_bytes=1, chapter="Глава 1", role=role, actor_name=actor,
                             kind="audition", canonical_filename=f"{aid}.wav", uploaded_at=when))
        db.add(AuditionReaction(audio_file_id="a1", voter_uid="author", voter_name="Белозёров Александр", value=1))
        db.commit()
    return factory


def _client(roles, name="Кто-то"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "x", "sub": "x", "roles": roles, "display_name": name}))
    return client


def test_the_feed_holds_every_book_newest_first_with_the_role_state(factory):
    body = _client(["admin"]).get("/api/auditions/feed").json()
    assert [item["id"] for item in body["items"]] == ["a3", "a1", "a2"]
    first, maura = body["items"][0], body["items"][1]
    assert (first["book_title"], first["character_id"], first["role_actor"], first["role_auditions"]) == \
        ("Сказки волшебников. Книга 3", "c2", "", 1)
    assert (maura["role_actor"], maura["role_auditions"], maura["author_reaction"]) == ("Кравченко Алёна", 2, 1)
    assert body["can_approve"] is True and body["can_react"] is True


def test_the_author_may_react_and_approve(factory):
    body = _client(["author"]).get("/api/auditions/feed").json()
    assert body["can_react"] is True and body["can_approve"] is True


def test_an_agent_listens_but_does_not_approve(factory):
    body = _client(["agent"]).get("/api/auditions/feed").json()
    assert len(body["items"]) == 3 and body["can_approve"] is False


def test_a_dictor_sees_no_reactions_on_others(factory):
    body = _client(["dictor"], name="Ветрова Ольга").get("/api/auditions/feed").json()
    assert {item["id"]: item["author_reaction"] for item in body["items"]}["a1"] is None


def test_new_since_counts_only_fresh_ones(factory):
    r = _client(["admin"]).get("/api/auditions/feed", params={"since": "2026-10-02T00:00:00Z"}).json()
    assert r["new_count"] == 2


def test_anonymous_gets_401(factory):
    assert TestClient(app).get("/api/auditions/feed").status_code == 401
