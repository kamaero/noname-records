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
from sqlalchemy.pool import StaticPool
from collections import defaultdict
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routers.frontend import register_frontend_routes

from app.api.frontend_core import build_frontend_core_handlers
from app.auth import (
    is_authenticated, is_owner_telegram, session_auth_source, session_payload,
    session_roles, session_serializer, session_telegram_user_id,
)
from app.config import settings
from app.db import Base
from app.models import TelegramAuthAccount, User, UserRole
from app.services.user_admin import create_user, verify_password


class FakeRequest:
    def __init__(self, body=None, *, uid="owner-1", authenticated=True, roles=None):
        self._body = body or {}
        self.cookies = {"session": session_serializer.dumps({
            "uid": uid, "sub": uid, "roles": roles if roles is not None else ["admin"], "auth_source": "password",
        })} if authenticated else {}

    async def json(self):
        return self._body


def _handlers(SessionLocal, *, allowed=True):
    audited: list[dict] = []

    def audit(db, request, **kwargs):
        audited.append(kwargs)

    handlers = build_frontend_core_handlers(
        {
            "is_authenticated": is_authenticated,
            "is_owner_telegram": is_owner_telegram,
            "session_payload": session_payload,
            "session_roles": session_roles,
            "session_auth_source": session_auth_source,
            "session_telegram_user_id": session_telegram_user_id,
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
def store(monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", "11")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as db:
        db.add(User(id="owner-1", display_name="Max Ray", password_hash="x", is_active="true", login="max.ray"))
        db.add(UserRole(user_id="owner-1", role="admin"))
        db.add(TelegramAuthAccount(telegram_user_id="11", role="admin", display_name="Max Ray", user_id="owner-1", is_active="true"))
        db.commit()
        owner_id = db.query(User).filter(User.login == "max.ray").first().id
    return SessionLocal, owner_id


def _body(response):
    return json.loads(bytes(response.body).decode())


def test_creating_an_account_returns_the_password_once(store):
    SessionLocal, owner_id = store
    handlers, audited = _handlers(SessionLocal)

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

    response = run(handlers["api_create_user"](FakeRequest({"display_name": "Кто-то", "roles": ["author"]}, uid="dictor", roles=["dictor"])))

    assert response.status_code == 403


def test_renaming_reaches_the_database(store):
    SessionLocal, owner_id = store
    handlers, audited = _handlers(SessionLocal)
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
    handlers, _ = _handlers(SessionLocal)

    response = run(handlers["api_delete_user"](FakeRequest(), owner_id))

    assert response.status_code == 400
    assert _body(response)["error"] == "self_delete"
    with SessionLocal() as db:
        assert db.get(User, owner_id) is not None


def test_a_new_password_is_returned_and_takes_effect(store):
    SessionLocal, owner_id = store
    handlers, _ = _handlers(SessionLocal)
    with SessionLocal() as db:
        made = create_user(db, display_name="Лада Тестова", roles=["dictor"])
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
    handlers, _ = _handlers(SessionLocal)
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
    handlers, _ = _handlers(SessionLocal)
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
    handlers, audited = _handlers(SessionLocal)
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
        assert db.query(TelegramAuthAccount).filter_by(telegram_user_id="900000101").one().user_id == keep["id"]
    assert any(item["action"] == "merge_users" for item in audited)


def test_a_refused_merge_says_why(store):
    SessionLocal, owner_id = store
    handlers, _ = _handlers(SessionLocal)

    response = run(handlers["api_merge_users"](FakeRequest({"keep_id": owner_id, "drop_id": owner_id})))

    assert response.status_code == 400
    assert _body(response)["error"] == "same_account"


ACCESS_HANDLERS = [
    ("api_users", False, ()),
    ("api_create_user", True, ()),
    ("api_update_user", True, ("owner-1",)),
    ("api_reset_user_password", True, ("owner-1",)),
    ("api_delete_user", True, ("owner-1",)),
    ("api_merge_users", True, ()),
    ("api_bulk_user_roles", True, ()),
    ("api_attach_telegram", True, ("owner-1",)),
    ("api_save_telegram_whitelist", True, ()),
    ("api_bot_reach", False, ()),
    ("api_users_export", False, ()),
    ("api_users_issue_passwords", True, ()),
]


@pytest.mark.parametrize("name,is_async,args", ACCESS_HANDLERS)
@pytest.mark.parametrize("authenticated,status", [(True, 403), (False, 401)])
def test_every_access_route_requires_the_owner_even_for_an_admin(store, name, is_async, args, authenticated, status):
    SessionLocal, _ = store
    with SessionLocal() as db:
        db.add(User(id="second", login="second", password_hash="x", display_name="Max Ray", is_active="true"))
        db.add(UserRole(user_id="second", role="admin"))
        db.commit()
    handlers, audited = _handlers(SessionLocal)
    request = FakeRequest({"display_name": "Новый Диктор", "roles": ["admin"]}, uid="second", authenticated=authenticated)
    result = handlers[name](request, *args)
    response = run(result) if is_async else result
    assert response.status_code == status
    assert audited == []
    with SessionLocal() as db:
        assert db.query(User).count() == 2
        assert db.query(TelegramAuthAccount).count() == 1
        assert db.get(User, "owner-1").display_name == "Max Ray"


def test_owner_by_password_can_update_the_telegram_whitelist(store):
    SessionLocal, _ = store
    handlers, _ = _handlers(SessionLocal)
    result = run(handlers["api_save_telegram_whitelist"](FakeRequest({
        "telegram_user_id": "77", "display_name": "Новый Диктор", "role": "dictor",
    })))
    assert result["ok"] is True
    with SessionLocal() as db:
        assert db.query(TelegramAuthAccount).filter_by(telegram_user_id="77").one().user_id


@pytest.mark.parametrize("uid,expected", [("owner-1", True), ("second", False)])
def test_me_exposes_owner_only_account_management(store, uid, expected):
    SessionLocal, _ = store
    handlers, _ = _handlers(SessionLocal)
    assert handlers["api_me"](FakeRequest(uid=uid))["can_manage_users"] is expected


def test_the_owner_keeps_account_management_after_telegram_login(store):
    SessionLocal, _ = store
    handlers, _ = _handlers(SessionLocal)
    request = FakeRequest()
    request.cookies = {"session": session_serializer.dumps({
        "uid": "owner-1", "sub": "owner", "roles": ["admin"],
        "auth_source": "telegram", "telegram_user_id": "11",
    })}
    assert handlers["api_me"](request)["can_manage_users"] is True
    assert handlers["api_users"](request)["users"]


def test_anonymous_me_does_not_offer_account_management(store):
    SessionLocal, _ = store
    handlers, _ = _handlers(SessionLocal)
    response = handlers["api_me"](FakeRequest(authenticated=False))
    assert response.status_code == 401
    assert _body(response)["can_manage_users"] is False


ACCESS_ROUTES = [
    ("GET", "/api/users"),
    ("POST", "/api/users"),
    ("PATCH", "/api/users/owner-1"),
    ("DELETE", "/api/users/owner-1"),
    ("POST", "/api/users/owner-1/password"),
    ("POST", "/api/users/merge"),
    ("POST", "/api/users/bulk-roles"),
    ("POST", "/api/users/owner-1/telegram"),
    ("POST", "/api/users/telegram-whitelist"),
    ("GET", "/api/users/bot-reach"),
    ("GET", "/api/users/export.txt"),
    ("POST", "/api/users/issue-passwords"),
]


@pytest.mark.parametrize("method,path", ACCESS_ROUTES)
def test_registered_account_routes_reject_a_nonowner_admin(store, method, path):
    SessionLocal, _ = store
    handlers, _ = _handlers(SessionLocal)
    registered = defaultdict(lambda: lambda: {}, handlers)
    app = FastAPI()
    register_frontend_routes(app, handlers=registered)
    client = TestClient(app)
    client.cookies.update(FakeRequest(uid="second").cookies)
    assert client.request(method, path, json={}).status_code == 403


def test_installation_owner_manages_users_without_telegram(store, monkeypatch):
    SessionLocal, _ = store
    monkeypatch.setattr(settings, "owner_telegram_id", "")
    monkeypatch.setattr(settings, "admin_login", "max.ray")
    handlers, _ = _handlers(SessionLocal)
    assert handlers["api_me"](FakeRequest())["can_manage_users"] is True
    assert handlers["api_users"](FakeRequest())["users"]
    assert handlers["api_users"](FakeRequest(uid="second")).status_code == 403


def test_configured_telegram_owner_does_not_fall_back_to_the_installation_admin(store, monkeypatch):
    SessionLocal, _ = store
    monkeypatch.setattr(settings, "admin_login", "second")
    with SessionLocal() as db:
        db.add(User(id="second", login="second", password_hash="x", display_name="Second Admin", is_active="true"))
        db.add(UserRole(user_id="second", role="admin"))
        db.commit()
    handlers, _ = _handlers(SessionLocal)
    assert handlers["api_me"](FakeRequest(uid="second"))["can_manage_users"] is False
