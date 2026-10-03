"""Маршруты раздела «Дикторы»: кто что видит и правит, аудио демо с Range."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import dictors as dictors_api
from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import DictorDemo, DictorProfile, User, UserRole
from app.services import audio_storage


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(dictors_api, "SessionLocal", factory)
    with factory() as db:
        db.add(User(id="admin", login="admin", password_hash="x", display_name="Админ", is_active="true"))
        db.add(UserRole(user_id="admin", role="admin"))
        db.add(User(id="u1", login="olga", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
        db.add(UserRole(user_id="u1", role="dictor"))
        audio_storage.write_file("demos/u1/d1.mp3", b"0123456789")
        db.add(DictorDemo(id="d1", user_id="u1", title="сказка", stored_key="demos/u1/d1.mp3",
                          duration_seconds=60, size_bytes=10, md5="m", source="import"))
        db.add(DictorDemo(id="gone", user_id="u1", title="пропал", stored_key="demos/u1/gone.mp3",
                          duration_seconds=60, size_bytes=10, md5="g", source="import"))
        db.commit()
    return factory


def _client(roles, uid="admin"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": uid, "sub": uid, "roles": roles, "display_name": "Кто-то"}))
    return client


@pytest.mark.parametrize("roles", [["dictor"], ["agent"]])
def test_dictors_and_agents_are_kept_out(factory, roles):
    api = _client(roles, uid="u1")
    assert api.get("/api/dictors").status_code == 403
    assert api.get("/api/dictors/u1").status_code == 403
    assert api.get("/api/dictors/demos/d1/audio").status_code == 403
    assert api.patch("/api/dictors/u1/note", json={"note": "x"}).status_code == 403


def test_anonymous_gets_401(factory):
    assert TestClient(app).get("/api/dictors").status_code == 401


def test_the_author_reads_and_writes_the_note_but_nothing_else(factory):
    api = _client(["author"], uid="u1")
    assert [i["name"] for i in api.get("/api/dictors").json()["items"]] == ["Ветрова Ольга"]
    card = api.get("/api/dictors/u1").json()
    assert card["name"] == "Ветрова Ольга" and "reachable" in card
    assert api.patch("/api/dictors/u1/note", json={"note": "звонкая"}).status_code == 200
    with factory() as db:
        assert db.get(DictorProfile, "u1").note == "звонкая"
    assert api.post("/api/dictors", json={"name": "Новый Диктор"}).status_code == 403
    assert api.patch("/api/dictors/u1", json={"name": "Другое Имя"}).status_code == 403
    assert api.delete("/api/dictors/u1").status_code == 403
    assert api.post("/api/dictors/u1/main-demo", json={"demo_id": "d1"}).status_code == 403
    assert api.delete("/api/dictors/demos/d1").status_code == 403


def test_the_admin_creates_renames_stars_and_deletes(factory):
    api = _client(["admin"])
    made = api.post("/api/dictors", json={"name": "Петров Пётр", "telegram_user_id": "77", "username": "@petr"})
    assert made.status_code == 200 and made.json()["user_id"]
    assert api.post("/api/dictors", json={"name": "Пётр Петров"}).json()["error"] == "name_taken"
    assert api.patch("/api/dictors/u1", json={"name": "Ветрова-Лунная Ольга"}).status_code == 200
    assert api.post("/api/dictors/u1/main-demo", json={"demo_id": "d1"}).status_code == 200
    assert api.delete("/api/dictors/demos/d1").status_code == 200
    gone = api.delete(f"/api/dictors/{made.json()['user_id']}")
    assert gone.status_code == 200
    with factory() as db:
        assert db.get(User, "u1").display_name == "Ветрова-Лунная Ольга"
        assert db.get(DictorDemo, "d1") is None
        assert db.get(User, made.json()["user_id"]) is None
    assert api.get("/api/dictors/nobody").status_code == 404


def test_the_demo_plays_whole_and_by_range(factory):
    api = _client(["author"], uid="u1")
    whole = api.get("/api/dictors/demos/d1/audio")
    assert whole.status_code == 200 and whole.content == b"0123456789"
    assert whole.headers["content-type"] == "audio/mpeg"
    part = api.get("/api/dictors/demos/d1/audio", headers={"Range": "bytes=2-5"})
    assert part.status_code == 206 and part.content == b"2345"
    assert api.get("/api/dictors/demos/gone/audio").json()["error"] == "audio_file_missing"


def test_an_uploaded_demo_goes_through_the_converter(factory, monkeypatch):
    def fake_mp3(src, target):
        with open(target, "wb") as handle:
            handle.write(b"mp3")
        return {"duration_seconds": 30.0, "size_bytes": 3, "md5": "new", "trimmed": False}
    monkeypatch.setattr("app.services.demo_audio.to_demo_mp3", fake_mp3)
    api = _client(["admin"])
    r = api.post("/api/dictors/u1/demos", files={"file": ("сказка.wav", b"raw", "audio/wav")}, data={"title": "сказка"})
    assert r.status_code == 200 and r.json()["demo"]["title"] == "сказка"


def test_a_rename_that_fails_midway_puts_the_files_back(factory, monkeypatch):
    # Ревью 01.10: закрытие сессии без rollback не откатывает переезд файлов.
    from app.models import AudioFile
    with factory() as db:
        audio_storage.write_file("K/1/take/old.wav", b"take")
        db.add(AudioFile(id="a1", book_code="K", original_filename="t.wav", stored_key="K/1/take/old.wav",
                         mime_type="audio/wav", size_bytes=4, chapter="1", role="Куйбу", actor_name="Ветрова Ольга",
                         kind="take", location="local", canonical_filename="old.wav"))
        db.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("mid-rename")
    monkeypatch.setattr("app.services.casting.rebuild_assignments", boom)
    from app.models import Character, ScriptBook
    with factory() as db:
        db.add(ScriptBook(id="b1", title="K", display_title="K", source_filename="x", source_format="docx", total_chars=0,
                          author_sheets_x1000=0, chapter_count=1, has_chapters="true", status="processing"))
        db.add(Character(id="c1", book_id="b1", name="Куйбу", aliases="", character_color="", actor_name="Ветрова Ольга"))
        db.commit()
    api = TestClient(app, raise_server_exceptions=False)
    api.cookies.set("session", session_serializer.dumps({"uid": "admin", "sub": "admin", "roles": ["admin"], "display_name": "А"}))
    assert api.patch("/api/dictors/u1", json={"name": "Совсем Другая"}).status_code == 500
    import os
    assert os.path.isfile(audio_storage.resolve_path("K/1/take/old.wav"))
