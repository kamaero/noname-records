from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth_routes
from app.db import Base
from app.models import AuditLog, TelegramAuthAccount, User, UserRole
from app.services.login_identity import TELEGRAM_PASSWORD_HASH
from app.services.passwords import verify_password
from app.services.user_admin import (
    BOT_PASSWORD_ACTION, bot_passwords_issued_last_hour, issue_password_by_bot,
)

NOW = datetime(2026, 10, 1, 12, 0, 0)


@pytest.fixture()
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _user(db, *, password_hash=TELEGRAM_PASSWORD_HASH):
    db.add(User(id="u1", login="tg_42", password_hash=password_hash, display_name="Ольга Ветрова", is_active="true"))
    db.add(UserRole(id="r1", user_id="u1", role="dictor"))
    db.commit()


def test_the_bot_password_replaces_the_hash_and_is_journalled_without_the_password(factory):
    with factory() as db:
        _user(db)
        issued = issue_password_by_bot(db, "u1", now=NOW)
        db.commit()
        user = db.get(User, "u1")
        assert issued["login"] == "tg_42" and len(issued["password"]) == 12
        assert verify_password(issued["password"], user.password_hash)
        row = db.query(AuditLog).one()
        assert row.action == BOT_PASSWORD_ACTION and row.user_id == "u1"
        assert issued["password"] not in row.payload_json


def test_issues_are_counted_for_the_last_hour_only(factory):
    with factory() as db:
        _user(db)
        issue_password_by_bot(db, "u1", now=NOW - timedelta(minutes=61))
        issue_password_by_bot(db, "u1", now=NOW - timedelta(minutes=5))
        db.commit()
        assert bot_passwords_issued_last_hour(db, "u1", now=NOW) == 1


def _login_client(factory, monkeypatch):
    monkeypatch.setattr(auth_routes, "SessionLocal", factory)
    monkeypatch.setattr(auth_routes.limiter, "enabled", False)
    app = FastAPI()
    app.state.limiter = auth_routes.limiter
    auth_routes.register_auth_routes(
        app, verify_password_cb=verify_password, audit_cb=lambda *a, **k: None,
        get_user_roles_cb=lambda db, uid: [r.role for r in db.query(UserRole).filter(UserRole.user_id == uid)],
        needs_password_setup_cb=lambda: False, verify_telegram_payload_cb=lambda payload: (True, ""),
    )
    return TestClient(app, follow_redirects=False)


def test_the_bot_password_signs_in_and_telegram_login_still_works(factory, monkeypatch):
    with factory() as db:
        _user(db)
        db.add(TelegramAuthAccount(telegram_user_id="42", role="dictor", access_scope="full",
                                   display_name="Ольга Ветрова", is_active="true", user_id="u1"))
        issued = issue_password_by_bot(db, "u1", now=NOW)
        db.commit()
    api = _login_client(factory, monkeypatch)
    by_password = api.post("/login", data={"login": issued["login"], "password": issued["password"]})
    assert by_password.headers["location"] == "/app/books"
    by_telegram = api.post("/auth/telegram", data={"id": "42", "auth_date": "1", "hash": "x", "first_name": "Ольга"})
    assert by_telegram.headers["location"] == "/app/books"
