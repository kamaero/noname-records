"""Кого пускают в раздел записи — проверено самим входом, а не чтением исходников.

Файл раньше держал собственную копию списка ролей — `{"admin", "dictor_pro",
"dictor_neo"}` — и сверял её саму с собой. Копия ничего не знала о настоящей проверке и
осталась зелёной, когда та уже отвечала 403 всем дикторам сразу.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.auth import session_serializer
from app.db import Base, SessionLocal, engine as app_engine
from app.main import app

PREVIEW = "/dictor-pro/batch-validate"
BODY = {"book_code": "КП", "actor_name": "Роман Сомов", "files": [], "overrides": []}


@pytest.fixture(autouse=True)
def _database():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal.configure(bind=engine)
    try:
        yield
    finally:
        SessionLocal.configure(bind=app_engine)


def _status(roles):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "u1", "sub": "u1", "roles": list(roles), "display_name": "Кто-то"}))
    return client.post(PREVIEW, json=BODY).status_code


@pytest.mark.parametrize("role", ["admin", "author", "dictor"])
def test_the_three_roles_of_the_studio_are_let_in(role):
    assert _status([role]) == 200


@pytest.mark.parametrize("role", ["dictor_pro", "dictor_neo"])
def test_a_role_the_studio_abolished_is_nobody(role):
    assert _status([role]) == 403


def test_a_session_with_no_role_at_all_is_turned_away():
    assert _status([]) == 403
