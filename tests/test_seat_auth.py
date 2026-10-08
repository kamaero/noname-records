"""Вход по ключу запуска: логина нет, но сайт на 127.0.0.1 не достаётся чужой странице."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.api import seat as seat_api
from app.config import settings
from app.db import Base
from app.main import app

TOKEN = "t" * 48


@pytest.fixture()
def one_seat(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    # ручки получают один и тот же SessionLocal через реестр зависимостей при импорте —
    # переподвязываем его к базе теста, а не подменяем имя в одном модуле
    from app.db import SessionLocal
    old_bind = SessionLocal.kw["bind"]
    SessionLocal.configure(bind=engine)
    monkeypatch.setattr(settings, "seat_mode", "one")
    monkeypatch.setattr(settings, "seat_token", TOKEN)
    yield
    SessionLocal.configure(bind=old_bind)


def _client():
    return TestClient(app, base_url="http://127.0.0.1:51234")


def test_right_token_opens_a_session(one_seat):
    client = _client()
    response = client.get(f"/seat?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"] == "/app/"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    me = client.get("/api/me").json()
    assert me["authenticated"] and "admin" in me["roles"] and "author" in me["roles"]


@pytest.mark.parametrize("token", ["", "wrong", TOKEN[:-1]])
def test_wrong_token_is_refused(one_seat, token):
    response = _client().get(f"/seat?token={token}", follow_redirects=False)
    assert response.status_code == 401 and "set-cookie" not in response.headers


def test_no_session_no_api(one_seat):
    assert _client().get("/api/books").status_code == 401


def test_a_foreign_page_cannot_post_without_the_session(one_seat):
    # чужая страница в браузере шлёт POST на 127.0.0.1: cookie SameSite=Strict она не
    # получит, а без неё — отказ
    assert _client().post("/api/books/upload", data={}).status_code in (401, 403)


def test_foreign_host_is_refused_even_with_a_session(one_seat):
    client = _client()
    client.get(f"/seat?token={TOKEN}", follow_redirects=False)
    assert client.get("/api/books", headers={"host": "evil.example:51234"}).status_code == 400
    assert client.get("/api/books", headers={"host": "localhost:51234"}).status_code == 200


def test_a_cookie_from_the_previous_launch_is_dead(one_seat, monkeypatch):
    client = _client()
    client.get(f"/seat?token={TOKEN}", follow_redirects=False)
    monkeypatch.setattr(settings, "seat_token", "n" * 48)
    assert client.get("/api/books").status_code == 401


def test_a_studio_cookie_does_not_open_the_seat(one_seat):
    # сессия VPS-формата (без отпечатка ключа запуска) — не сессия этого запуска
    from app.auth import session_serializer
    client = _client()
    client.cookies.set("session", session_serializer.dumps({"uid": "u", "sub": "u", "roles": ["admin"]}))
    assert client.get("/api/books").status_code == 401


def test_seat_route_does_not_exist_in_studio(monkeypatch):
    monkeypatch.setattr(settings, "seat_mode", "studio")
    assert TestClient(app).get(f"/seat?token={TOKEN}", follow_redirects=False).status_code == 404


def test_owner_account_is_created_once(one_seat):
    client = _client()
    client.get(f"/seat?token={TOKEN}", follow_redirects=False)
    client.get(f"/seat?token={TOKEN}", follow_redirects=False)
    from app.models import User, UserRole
    with seat_api.SessionLocal() as db:
        assert db.query(User).count() == 1
        assert {r.role for r in db.query(UserRole)} == {"admin", "author"}
