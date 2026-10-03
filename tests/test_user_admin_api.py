"""The account endpoints behind the «Пользователи» screen.

The service under them is tested on its own; what these check is the part a screen
can get wrong: who is allowed in, what comes back to be shown once (the password),
and that a refusal arrives as a refusal rather than a 500.
"""
import json
from asyncio import run

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.frontend_core import build_frontend_core_handlers
from app.db import Base
from app.models import TelegramAuthAccount, User, UserRole
from app.services.user_admin import create_user, verify_password


class FakeRequest:
    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


def _handlers(SessionLocal, *, allowed=True, uid="owner-1"):
    audited: list[dict] = []

    def audit(db, request, **kwargs):
        audited.append(kwargs)

    handlers = build_frontend_core_handlers(
        {
            "is_authenticated": lambda _request: True,
            "is_owner_telegram": lambda _request: False,
            "session_payload": lambda _request: {"uid": uid, "sub": "owner", "display_name": "Max Ray"},
            "session_roles": lambda _request: {"admin"},
            "session_auth_source": lambda _request: "password",
            "session_telegram_user_id": lambda _request: "",
            "owner_api_allowed": lambda _request: allowed,
            "has_workspace_full_access": lambda _request: True,
            "has_workspace_neo_only_access": lambda _request: False,
            "workspace_tabs_for_request": lambda _request: [],
            "SessionLocal": SessionLocal,
            "build_owner_dashboard_data": lambda db, _x: {"owner_whitelist_accounts": []},
            "User": User,
            "TelegramAuthAccount": TelegramAuthAccount,
            "get_user_roles": lambda db, user_id: [r.role for r in db.query(UserRole).filter(UserRole.user_id == user_id)],
            "format_dt": lambda value: str(value) if value else None,
            "audit": audit,
            "settings": type("S", (), {"telegram_bot_username": "", "admin_password_hash": "x"})(),
            "WORKSPACE_TABS": [],
            "needs_password_setup": lambda: False,
        }
    )
    return handlers, audited


@pytest.fixture()
def store():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as db:
        create_user(db, display_name="Max Ray", roles=["admin"], login="max.ray")
        db.commit()
        owner_id = db.query(User).filter(User.login == "max.ray").first().id
    return SessionLocal, owner_id


def _body(response):
    return json.loads(bytes(response.body).decode())


def test_creating_an_account_returns_the_password_once(store):
    SessionLocal, owner_id = store
    handlers, audited = _handlers(SessionLocal, uid=owner_id)

    response = run(handlers["api_create_user"](FakeRequest({"display_name": "Мирон Зарецкий", "roles": ["dictor"]})))
    payload = _body(response)

    assert payload["ok"] is True
    assert payload["user"]["login"] == "miron.zaretskiy"
    with SessionLocal() as db:
        stored = db.query(User).filter(User.login == "miron.zaretskiy").first()
        assert verify_password(payload["password"], stored.password_hash)
    assert any(item["action"] == "create_user" for item in audited)


def test_a_dictor_cannot_open_the_account_screen(store):
    SessionLocal, owner_id = store
    handlers, _ = _handlers(SessionLocal, allowed=False)

    response = run(handlers["api_create_user"](FakeRequest({"display_name": "Кто-то", "roles": ["author"]})))

    assert response.status_code == 403


def test_renaming_reaches_the_database(store):
    SessionLocal, owner_id = store
    handlers, audited = _handlers(SessionLocal, uid=owner_id)
    with SessionLocal() as db:
        made = create_user(db, display_name="Михаил Галинов", roles=["dictor"])
        db.commit()
        user_id = made["user"]["id"]

    response = run(handlers["api_update_user"](FakeRequest({"display_name": "Михаил Галанов"}), user_id))

    assert _body(response)["user"]["display_name"] == "Михаил Галанов"
    with SessionLocal() as db:
        assert db.get(User, user_id).display_name == "Михаил Галанов"
    assert any(item["action"] == "update_user" for item in audited)


def test_a_refused_deletion_comes_back_as_a_refusal_not_a_crash(store):
    SessionLocal, owner_id = store
    handlers, _ = _handlers(SessionLocal, uid=owner_id)

    response = run(handlers["api_delete_user"](FakeRequest(), owner_id))

    assert response.status_code == 400
    assert _body(response)["error"] == "self_delete"
    with SessionLocal() as db:
        assert db.get(User, owner_id) is not None


def test_a_new_password_is_returned_and_takes_effect(store):
    SessionLocal, owner_id = store
    handlers, _ = _handlers(SessionLocal, uid=owner_id)
    with SessionLocal() as db:
        made = create_user(db, display_name="Зарина Мельникова", roles=["dictor"])
        db.commit()
        user_id, old = made["user"]["id"], made["password"]

    response = run(handlers["api_reset_user_password"](FakeRequest(), user_id))

    fresh = _body(response)["password"]
    with SessionLocal() as db:
        stored = db.get(User, user_id)
        assert verify_password(fresh, stored.password_hash)
        assert not verify_password(old, stored.password_hash)


def test_the_whole_troupe_can_be_moved_off_the_author_role_at_once(store):
    """49 dictors were given `author`, which is the right to edit the markup."""
    SessionLocal, owner_id = store
    handlers, _ = _handlers(SessionLocal, uid=owner_id)
    with SessionLocal() as db:
        ids = [create_user(db, display_name=f"Диктор {n}", roles=["author"])["user"]["id"] for n in range(3)]
        db.commit()

    response = run(handlers["api_bulk_user_roles"](FakeRequest({"ids": ids, "roles": ["dictor"]})))

    assert _body(response)["changed"] == 3
    with SessionLocal() as db:
        for user_id in ids:
            assert [row.role for row in db.query(UserRole).filter(UserRole.user_id == user_id)] == ["dictor"]
        # the owner keeps his own role: he was not in the list
        assert "admin" in [row.role for row in db.query(UserRole).filter(UserRole.user_id == owner_id)]


def test_the_list_says_how_each_account_gets_in(store):
    SessionLocal, owner_id = store
    handlers, _ = _handlers(SessionLocal, uid=owner_id)
    with SessionLocal() as db:
        create_user(db, display_name="Max Ray", login="tg_900000100", password_hash="telegram-login", roles=["author"])
        db.commit()

    payload = handlers["api_users"](FakeRequest())
    rows = {row["login"]: row for row in payload["users"]}

    assert rows["tg_900000100"]["auth"] == "telegram"
    assert rows["tg_900000100"]["twin_login"] == "max.ray"


def test_the_pair_is_joined_into_the_account_that_keeps_the_password(store):
    """The owner's rule: `elena.breeze` is what people wrote down, `tg_…` is a pseudonym."""
    SessionLocal, owner_id = store
    handlers, audited = _handlers(SessionLocal, uid=owner_id)
    with SessionLocal() as db:
        keep = create_user(db, display_name="Елена Breeze", roles=["dictor"])["user"]
        drop = create_user(db, display_name="Елена Breeze", login="tg_900000101",
                           password_hash="telegram-login", roles=["author"])["user"]
        db.add(TelegramAuthAccount(telegram_user_id="900000101", role="author", access_scope="full",
                                   display_name="Елена Breeze", is_active="true", user_id=drop["id"]))
        db.commit()

    response = run(handlers["api_merge_users"](FakeRequest({"keep_id": keep["id"], "drop_id": drop["id"]})))
    payload = _body(response)

    assert payload["ok"] is True and payload["dropped_login"] == "tg_900000101"
    with SessionLocal() as db:
        assert db.get(User, drop["id"]) is None
        assert db.query(TelegramAuthAccount).first().user_id == keep["id"]
    assert any(item["action"] == "merge_users" for item in audited)


def test_a_refused_merge_says_why(store):
    SessionLocal, owner_id = store
    handlers, _ = _handlers(SessionLocal, uid=owner_id)

    response = run(handlers["api_merge_users"](FakeRequest({"keep_id": owner_id, "drop_id": owner_id})))

    assert response.status_code == 400
    assert _body(response)["error"] == "same_account"
