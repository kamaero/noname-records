"""В «одном месте» нет учёток, Telegram, проб и сроков; на VPS всё на месте."""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

HIDDEN = [("get", "/api/users"), ("post", "/api/users/merge"), ("post", "/api/telegram/webhook"),
          ("post", "/auth/telegram"), ("get", "/api/me/bot-reach"), ("post", "/login"), ("post", "/logout"),
          ("get", "/api/auditions/feed"), ("post", "/api/v2/auditions/a1/reaction"),
          ("get", "/api/v2/books/b1/auditions"), ("post", "/api/deadlines/d1/extend"),
          ("post", "/dictor-pro/batch-validate")]
KEPT = [("get", "/api/dictors/picker"), ("get", "/api/me"), ("get", "/api/public/auth-config"),
        ("get", "/api/books"), ("get", "/api/v2/books/b1/cast")]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(settings, "seat_mode", "one")
    return TestClient(app, base_url="http://127.0.0.1:5000")


@pytest.mark.parametrize("method,path", HIDDEN)
def test_hidden_in_one_seat(client, method, path):
    response = getattr(client, method)(path)
    assert response.status_code == 404 and response.json()["error"] == "not_in_one_seat"


@pytest.mark.parametrize("method,path", KEPT)
def test_kept_in_one_seat(client, method, path):
    assert getattr(client, method)(path).status_code != 404


@pytest.mark.parametrize("method,path", HIDDEN)
def test_studio_still_has_them(monkeypatch, method, path):
    # 404 на VPS бывает и от самой ручки («книги нет», «бот не настроен») — проверяем, что
    # сторож не вмешался и что маршрут зарегистрирован
    monkeypatch.setattr(settings, "seat_mode", "studio")
    response = getattr(TestClient(app), method)(path)
    assert "not_in_one_seat" not in response.text
    from starlette.routing import Match
    scope = {"type": "http", "path": path, "method": method.upper()}
    assert any(route.matches(scope)[0] == Match.FULL for route in app.routes)


def test_a_prefix_does_not_swallow_its_neighbours():
    from app.seat import is_hidden
    assert is_hidden("/api/users/u1/password") and is_hidden("/login")
    assert not is_hidden("/api/users-stats") and not is_hidden("/login-help")
    assert not is_hidden("/api/v2/books/b1/auditions/extra") and not is_hidden("/api/dictors/picker")


def test_the_mode_reaches_the_frontend(client):
    assert client.get("/api/public/auth-config").json()["seat_mode"] == "one"


def test_studio_reports_studio(monkeypatch):
    monkeypatch.setattr(settings, "seat_mode", "studio")
    assert TestClient(app).get("/api/public/auth-config").json()["seat_mode"] == "studio"
