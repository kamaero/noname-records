"""The login routes, end to end: who gets a session cookie and who is turned away.

The service beside this decides which account a login belongs to; these tests check
that the routes actually ask. «Выключить» on the accounts screen used to set a flag
the password route never read — the person kept signing in — and that is the kind of
mistake only a test through the real endpoint catches.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth_routes
from app.db import Base
from app.models import TelegramAuthAccount, User, UserRole
from app.services.passwords import verify_password
from app.services.user_admin import create_user



@pytest.fixture()
def client(monkeypatch):
    # one in-memory database for every connection: the route runs on another thread,
    # and the default pool would hand it an empty one
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(auth_routes, "SessionLocal", SessionLocal)

    app = FastAPI()
    app.state.limiter = auth_routes.limiter
    # ten logins a minute is the production guard; these tests are about identity,
    # and sharing the limiter's counter across them makes the fourth one a 429
    monkeypatch.setattr(auth_routes.limiter, "enabled", False)
    auth_routes.register_auth_routes(
        app,
        verify_password_cb=verify_password,
        audit_cb=lambda *args, **kwargs: None,
        get_user_roles_cb=lambda db, user_id: [row.role for row in db.query(UserRole).filter(UserRole.user_id == user_id)],
        needs_password_setup_cb=lambda: False,
        verify_telegram_payload_cb=lambda payload: (True, ""),
    )
    return TestClient(app, follow_redirects=False), SessionLocal


def _seen_error(response) -> str:
    return response.headers.get("location", "")


class TestPasswordLogin:
    def test_a_live_account_gets_a_session(self, client):
        api, SessionLocal = client
        with SessionLocal() as db:
            made = create_user(db, display_name="Архип Дорохов", roles=["dictor"])
            db.commit()

        response = api.post("/login", data={"login": made["user"]["login"], "password": made["password"]})

        assert response.status_code == 302
        assert "session" in response.cookies

    def test_a_switched_off_account_is_turned_away_even_with_the_right_password(self, client):
        api, SessionLocal = client
        with SessionLocal() as db:
            made = create_user(db, display_name="Архип Дорохов", roles=["dictor"])
            db.commit()
            db.get(User, made["user"]["id"]).is_active = "false"
            db.commit()

        response = api.post("/login", data={"login": made["user"]["login"], "password": made["password"]})

        assert response.status_code == 302
        assert "session" not in response.cookies
        assert "login" in _seen_error(response)
        assert _seen_error(response).endswith("error=disabled")

    def test_the_address_carries_a_code_not_a_sentence(self, client):
        """Фраза в адресе — это текст, который любой мог бы подменить своим на странице входа."""
        api, _ = client
        response = api.post("/login", data={"login": "nobody", "password": "wrong"})
        assert _seen_error(response) == "/app/login?error=credentials"

    def test_a_failed_telegram_signature_says_telegram_not_why(self, monkeypatch):
        """Подробная причина отказа Telegram остаётся в журнале, странице — только код."""
        app = FastAPI()
        app.state.limiter = auth_routes.limiter
        monkeypatch.setattr(auth_routes.limiter, "enabled", False)
        auth_routes.register_auth_routes(
            app,
            verify_password_cb=verify_password,
            audit_cb=lambda *args, **kwargs: None,
            get_user_roles_cb=lambda db, user_id: [],
            needs_password_setup_cb=lambda: False,
            verify_telegram_payload_cb=lambda payload: (False, "Неверная подпись Telegram login"),
        )
        response = TestClient(app, follow_redirects=False).post(
            "/auth/telegram", data={"id": "1", "auth_date": "1", "hash": "x"})
        assert _seen_error(response) == "/app/login?error=telegram"


class TestTelegramLogin:
    def _whitelist(self, db, telegram_user_id: str, *, role: str = "author", name: str = "Елена Breeze"):
        db.add(TelegramAuthAccount(telegram_user_id=telegram_user_id, role=role, access_scope="full",
                                   display_name=name, is_active="true"))
        db.commit()

    def _post(self, api, telegram_user_id: str):
        return api.post(
            "/auth/telegram",
            data={"id": telegram_user_id, "auth_date": "1", "hash": "x", "first_name": "Elena", "last_name": "F"},
        )

    def test_a_linked_identity_signs_into_the_account_it_points_at(self, client):
        api, SessionLocal = client
        with SessionLocal() as db:
            made = create_user(db, display_name="Елена Breeze", roles=["dictor"])
            db.commit()
            self._whitelist(db, "900000101")
            db.query(TelegramAuthAccount).first().user_id = made["user"]["id"]
            db.commit()

        response = self._post(api, "900000101")

        assert response.status_code == 302
        with SessionLocal() as db:
            # no second account was invented for the same person
            assert db.query(User).count() == 1
            assert sorted(row.role for row in db.query(UserRole)) == ["dictor"]

    def test_a_switched_off_account_is_turned_away_even_though_the_whitelist_allows_it(self, client):
        api, SessionLocal = client
        with SessionLocal() as db:
            made = create_user(db, display_name="Елена Breeze", roles=["dictor"])
            db.commit()
            db.get(User, made["user"]["id"]).is_active = "false"
            self._whitelist(db, "900000101")
            db.query(TelegramAuthAccount).first().user_id = made["user"]["id"]
            db.commit()

        response = self._post(api, "900000101")

        assert "session" not in response.cookies

    def test_a_first_visit_creates_the_account_and_links_it(self, client):
        api, SessionLocal = client
        with SessionLocal() as db:
            self._whitelist(db, "777", role="dictor", name="Новый Диктор")

        response = self._post(api, "777")

        assert response.status_code == 302
        with SessionLocal() as db:
            user = db.query(User).filter(User.login == "tg_777").first()
            assert user is not None
            assert db.query(TelegramAuthAccount).first().user_id == user.id
