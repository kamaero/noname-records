"""«Смета» — только владельцу: по учётке, а не по имени (06.10)."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import TelegramAuthAccount, User
from app.services import payouts


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(payouts.settings, "owner_telegram_id", "11")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(User(id="u11", login="admin", password_hash="x", display_name="Примеряфеицшпчб Примерщюагоэсгв", is_active="true"))
        session.add(User(id="u99", login="namesake", password_hash="x", display_name="Примеряфеицшпчб Примерщюагоэсгв", is_active="true"))
        session.add(TelegramAuthAccount(telegram_user_id="11", role="admin", access_scope="full",
                                        display_name="Примеряфеицшпчб Примерщюагоэсгв", is_active="true", user_id="u11"))
        session.commit()
        yield session


def _request(monkeypatch, *, uid, source="password", tid=""):
    monkeypatch.setattr(payouts, "session_payload", lambda request: {"uid": uid, "sub": "x"})
    monkeypatch.setattr(payouts, "is_owner_telegram", lambda request: source == "telegram" and tid == "11")
    return SimpleNamespace()


def test_the_owner_by_password_or_telegram(db, monkeypatch):
    assert payouts.owner_user_id(db) == "u11"
    assert payouts.is_owner_request(db, _request(monkeypatch, uid="u11")) is True
    assert payouts.is_owner_request(db, _request(monkeypatch, uid="zzz", source="telegram", tid="11")) is True


def test_a_namesake_and_no_owner_setting_get_nothing(db, monkeypatch):
    assert payouts.is_owner_request(db, _request(monkeypatch, uid="u99")) is False
    monkeypatch.setattr(payouts.settings, "owner_telegram_id", "")
    assert payouts.is_owner_request(db, _request(monkeypatch, uid="u11")) is False
