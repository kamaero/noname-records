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
from app.models import AudioFile, AuditionScore, Character, ScriptBook
from app.services.audio_uploads import derive_book_code


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(feed_api, "SessionLocal", factory)
    with factory() as db:
        for bid, title in (("b1", "Карта Переправы"), ("b2", "Сад великанов. Книга 3")):
            db.add(ScriptBook(id=bid, title=title, display_title=title, source_filename="x", source_format="docx",
                              total_chars=0, author_sheets_x1000=0, chapter_count=1, has_chapters="true",
                              status="processing"))
        db.add(Character(id="c1", book_id="b1", name="Саяху", aliases="", character_color="", actor_name="Кравченко Алёна"))
        db.add(Character(id="c2", book_id="b2", name="Астрид", aliases="", character_color="", actor_name=""))
        for aid, book, role, actor, when in (
            ("a1", "Карта Переправы", "Саяху", "Кравченко Алёна", datetime(2026, 10, 2, 9)),
            ("a2", "Карта Переправы", "Саяху", "Ветрова Ольга", datetime(2026, 10, 1, 9)),
            ("a3", "Сад великанов. Книга 3", "Астрид", "Ветрова Ольга", datetime(2026, 10, 2, 12)),
        ):
            db.add(AudioFile(id=aid, book_code=derive_book_code(book), original_filename="p.wav", stored_key=f"k/{aid}",
                             mime_type="audio/wav", size_bytes=1, chapter="Глава 1", role=role, actor_name=actor,
                             kind="audition", canonical_filename=f"{aid}.wav", uploaded_at=when))
        for lane, uid, score in (("author", "author", 8), ("studio", "owner", 7)):
            db.add(AuditionScore(book_id="b1", role="Саяху", actor_name="Кравченко Алёна", lane=lane,
                                 voter_uid=uid, voter_name="Примеров Алексей", score=score))
        db.commit()
    return factory


def _client(roles, name="Кто-то"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "x", "sub": "x", "roles": roles, "display_name": name}))
    return client


def test_the_feed_holds_every_book_newest_first_with_the_role_state(factory):
    body = _client(["admin"]).get("/api/auditions/feed").json()
    assert [item["key"] for item in body["items"]] == [
        "b2|Астрид|Ветрова Ольга", "b1|Саяху|Кравченко Алёна", "b1|Саяху|Ветрова Ольга"
    ]
    first, maura = body["items"][0], body["items"][1]
    assert (first["book_title"], first["character_id"], first["role_actor"], first["role_auditions"]) == \
        ("Сад великанов. Книга 3", "c2", "", 1)
    assert (maura["role_actor"], maura["role_auditions"], maura["approved"]) == ("Кравченко Алёна", 2, True)
    assert maura["scores"]["author"] == {"score": 8, "name": "Примеров Алексей", "reason": "", "comment": ""}
    assert [a["id"] for a in maura["auditions"]] == ["a1"]
    assert body["can_approve"] is True and body["can_score"] == ["studio"]


def test_the_author_may_react_and_approve(factory):
    body = _client(["author"]).get("/api/auditions/feed").json()
    assert body["can_score"] == ["author"] and body["can_approve"] is True


def test_an_agent_listens_but_does_not_approve(factory):
    body = _client(["agent"]).get("/api/auditions/feed").json()
    assert len(body["items"]) == 3 and body["can_approve"] is False


def test_a_dictor_sees_no_reactions_on_others(factory):
    body = _client(["dictor"], name="Ветрова Ольга").get("/api/auditions/feed").json()
    assert body["items"][1]["scores"] is None and body["can_score"] == []


def test_new_since_counts_only_fresh_ones(factory):
    r = _client(["admin"]).get("/api/auditions/feed", params={"since": "2026-10-02T00:00:00Z"}).json()
    assert r["new_count"] == 2


def test_anonymous_gets_401(factory):
    assert TestClient(app).get("/api/auditions/feed").status_code == 401


def test_reject_and_take_back_by_hand(factory):
    client = _client(["admin"])
    pair = {"book_id": "b1", "role": "Саяху", "actor_name": "Ветрова Ольга"}

    body = client.post("/api/auditions/reject", json=pair).json()
    assert body["ok"] and body["rejection"]["status"] == "pending"
    feed = client.get("/api/auditions/feed").json()["items"]
    [row] = [p for p in feed if p["actor_name"] == "Ветрова Ольга" and p["role"] == "Саяху"]
    assert row["rejection"]["status"] == "pending"

    body = client.post("/api/auditions/reject", json={**pair, "cancel": True}).json()
    assert body["ok"] and body["rejection"] is None


def test_the_approved_actor_is_not_rejected(factory):
    r = _client(["admin"]).post("/api/auditions/reject",
                                json={"book_id": "b1", "role": "Саяху", "actor_name": "Кравченко Алёна"})
    assert r.status_code == 400 and r.json()["error"] == "approved"


def test_only_those_who_approve_may_reject(factory):
    pair = {"book_id": "b1", "role": "Саяху", "actor_name": "Ветрова Ольга"}
    assert _client(["agent"]).post("/api/auditions/reject", json=pair).status_code == 403
    assert _client(["dictor"]).post("/api/auditions/reject", json=pair).status_code == 403
    assert _client(["author"]).post("/api/auditions/reject", json=pair).status_code == 200
