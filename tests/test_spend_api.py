"""API трат, лимита и цен моделей: только администратор."""
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
from app.services import provider_keys, spend, step_models
from app.services.provider_checks import CheckResult


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    f = sessionmaker(bind=engine, autoflush=False)
    for module in (ai_api, provider_keys, step_models, spend):
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




def test_spend_routes_are_admin_only(factory):
    for client, code in ((TestClient(app), 401), (_client(["dictor"], uid="u1"), 403), (_client(["author"]), 403)):
        assert client.get("/api/settings/spend").status_code == code
        assert client.put("/api/settings/spend/limit", json={"limit_rub": 5}).status_code == code
        assert client.put("/api/settings/ai/prices", json={}).status_code == code


def test_the_month_and_the_list_of_months(factory):
    with factory() as db:
        spend.record(db, step="sound", provider="routerai", model="vendor/x", unit="tokens", input_units=1, output_units=1)
        db.commit()
    body = _client(["admin"]).get("/api/settings/spend").json()
    assert body["unknown_calls"] == 1 and body["months"] and body["month"] == body["months"][0]


@pytest.mark.parametrize("value", [-1, "много", None])
def test_a_bad_limit_is_refused(factory, value):
    assert _client(["admin"]).put("/api/settings/spend/limit", json={"limit_rub": value}).status_code == 400


def test_the_limit_is_saved_and_logged(factory):
    assert _client(["admin"]).put("/api/settings/spend/limit", json={"limit_rub": 5000}).status_code == 200
    assert _client(["admin"]).get("/api/settings/spend").json()["limit_rub"] == 5000
    with factory() as db:
        assert db.query(AuditLog).filter(AuditLog.entity_type == "spend_limit", AuditLog.action == "limit_set").count() == 1


@pytest.mark.parametrize("body", [
    {"provider": "routerai", "model": "vendor/x", "unit": "tokens", "price_out": 1, "currency": "RUB"},
    {"provider": "routerai", "model": "vendor/x", "unit": "tokens", "price_in": 1, "price_out": 1, "currency": "EUR"},
    {"provider": "routerai", "model": "", "unit": "tokens", "price_in": 1, "price_out": 1, "currency": "RUB"},
    {"provider": "routerai", "model": "vendor/x", "unit": "tokens", "price_in": -1, "price_out": 1, "currency": "RUB"},
])
def test_a_bad_price_is_refused(factory, body):
    assert _client(["admin"]).put("/api/settings/ai/prices", json=body).status_code == 400


def test_a_studio_price_shows_on_the_step_and_can_be_reset(factory):
    client = _client(["admin"])
    body = {"provider": "routerai", "model": "anthropic/claude-opus-5", "unit": "tokens",
            "price_in": 1200, "price_out": 6000, "currency": "RUB"}
    assert client.put("/api/settings/ai/prices", json=body).status_code == 200
    sound = next(s for s in client.get("/api/settings/ai").json()["steps"] if s["step"] == "sound")
    assert sound["price"]["source"] == "studio" and sound["price"]["price_in"] == 1200
    assert client.delete("/api/settings/ai/prices", params={"provider": "routerai", "model": "anthropic/claude-opus-5"}).status_code == 200
    sound = next(s for s in client.get("/api/settings/ai").json()["steps"] if s["step"] == "sound")
    assert sound["price"]["source"] == "none"


def test_a_catalog_step_shows_the_builtin_price(factory):
    attribution = next(s for s in _client(["admin"]).get("/api/settings/ai").json()["steps"] if s["step"] == "attribution")
    assert attribution["price"]["source"] == "builtin" and attribution["price"]["currency"] == "USD"
