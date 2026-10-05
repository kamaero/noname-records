"""API «Настройки → Нейросети»: только администратор, ключ не возвращается никогда."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import ai_settings as ai_api
from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import AuditLog, User, UserRole
from app.services import provider_keys, step_models
from app.services.provider_checks import CheckResult


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    for module in (ai_api, provider_keys, step_models):
        monkeypatch.setattr(module, "SessionLocal", f)
    monkeypatch.setattr(provider_keys, "env_value", lambda name, default="": default)
    for attr in provider_keys.SETTINGS_ATTRS.values():
        monkeypatch.setattr(provider_keys.settings, attr, "", raising=False)
    monkeypatch.setattr(ai_api, "check_key", lambda p, k, **kw: CheckResult("ok", "Работает."))
    monkeypatch.setattr(ai_api, "balance", lambda p, k, **kw: {"provider": p, "available": False, "amount": None,
                                                               "unit": "", "detail": "нет данных"})
    with f() as db:
        db.add(User(id="admin", login="admin", password_hash="x", display_name="Админ", is_active="true"))
        db.add(UserRole(user_id="admin", role="admin"))
        db.add(User(id="u1", login="olga", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
        db.add(UserRole(user_id="u1", role="dictor"))
        db.commit()
    return f


def _client(roles, uid="admin"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": uid, "sub": uid, "roles": roles, "display_name": "Кто-то"}))
    return client


SECRET = "sk-very-secret-key-0123456789"


def test_only_the_admin_gets_in(factory):
    assert _client(["dictor"], uid="u1").get("/api/settings/ai").status_code == 403
    assert TestClient(app).get("/api/settings/ai").status_code == 401


def test_saving_checks_and_never_echoes_the_key(factory, monkeypatch):
    monkeypatch.setattr(ai_api, "check_key", lambda p, k, **kw: CheckResult("ok", "Работает."))
    client = _client(["admin"])
    saved = client.put("/api/settings/ai/keys/deepseek", json={"key": SECRET})
    assert saved.status_code == 200 and saved.json()["check_status"] == "ok"
    for response in (saved, client.get("/api/settings/ai"), client.post("/api/settings/ai/keys/deepseek/check")):
        assert SECRET[:12] not in response.text and SECRET[-8:] not in response.text
    assert client.get("/api/settings/ai").json()["providers"][0]["last4"] == "6789"


def test_the_change_is_logged_without_the_value(factory, monkeypatch):
    monkeypatch.setattr(ai_api, "check_key", lambda p, k, **kw: CheckResult("bad_key", "Ключ не подходит."))
    _client(["admin"]).put("/api/settings/ai/keys/claude", json={"key": SECRET})
    with factory() as db:
        logs = db.query(AuditLog).filter(AuditLog.entity_type == "provider_key").all()
    assert [l.action for l in logs] == ["key_saved"] and SECRET[-8:] not in logs[0].payload_json


def test_a_step_takes_only_its_providers(factory):
    client = _client(["admin"])
    assert client.put("/api/settings/ai/steps/ambient_audio", json={"provider": "deepseek", "model": "x"}).status_code == 400
    ok = client.put("/api/settings/ai/steps/sound", json={"provider": "openrouter", "model": "vendor/m"})
    assert ok.status_code == 200
    step = next(s for s in client.get("/api/settings/ai").json()["steps"] if s["step"] == "sound")
    assert (step["provider"], step["model"], step["source"]) == ("openrouter", "vendor/m", "site")


def test_an_empty_key_is_refused(factory):
    assert _client(["admin"]).put("/api/settings/ai/keys/openai", json={"key": "   "}).status_code == 400
