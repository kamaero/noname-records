"""Ручки «Первых шагов»: только администратор; отметка — только для примера."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import first_steps as api
from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.services import book_import, provider_keys, spend, step_models


@pytest.fixture()
def factory(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    for module in (api, provider_keys, step_models, spend):
        monkeypatch.setattr(module, "SessionLocal", f)
    monkeypatch.setattr(provider_keys, "env_value", lambda name, default="": default)
    for attr in provider_keys.SETTINGS_ATTRS.values():
        monkeypatch.setattr(provider_keys.settings, attr, "", raising=False)
    monkeypatch.setattr(book_import, "BOOK_SOURCE_DIR", tmp_path / "sources")
    provider_keys.clear_cache()
    step_models.clear_cache()
    return f


def _client(roles, uid="admin"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": uid, "sub": uid, "roles": roles, "display_name": "Кто-то"}))
    return client


def test_routes_are_admin_only(factory):
    for client, code in ((TestClient(app), 401), (_client(["dictor"], uid="u1"), 403)):
        assert client.get("/api/first-steps").status_code == code
        assert client.post("/api/first-steps/sample").status_code == code
        assert client.post("/api/first-steps/seen", json={"book_id": "x"}).status_code == code
        assert client.put("/api/first-steps", json={"hidden": True}).status_code == code


def test_import_twice_gives_one_book_and_seen_marks_only_it(factory):
    admin = _client(["admin"])
    first = admin.post("/api/first-steps/sample").json()["book_id"]
    assert admin.post("/api/first-steps/sample").json()["book_id"] == first
    assert admin.post("/api/first-steps/seen", json={"book_id": "other"}).json() == {"ok": False}
    assert admin.post("/api/first-steps/seen", json={"book_id": first}).json() == {"ok": True}
    steps = {s["key"]: s for s in admin.get("/api/first-steps").json()["steps"]}
    assert steps["sample"]["done"] and steps["result"]["done"]


def test_flags_persist(factory):
    admin = _client(["admin"])
    body = admin.put("/api/first-steps", json={"no_limit": True}).json()
    assert {s["key"]: s for s in body["steps"]}["limit"]["done"] is True
    body = admin.put("/api/first-steps", json={"hidden": True}).json()
    assert body["hidden"] is True and body["visible"] is False
    assert admin.get("/api/first-steps").json()["hidden"] is True


def test_bad_flag_body_is_rejected(factory):
    assert _client(["admin"]).put("/api/first-steps", json={"hidden": "yes"}).status_code == 400
