"""Спорные места — очередь редактора, а не дикторский экран.

Список собирается по всем главам книги, опубликованным и нет: диктору он открыл бы
реплики, которые автор ещё правит, и чужие роли с сомнениями модели. Интерфейс
просит его только у редактора, и ручка обязана думать так же.
"""
import pytest
from fastapi.testclient import TestClient

from app.auth import session_serializer
from app.main import app
from tests.consilium_book import BOOK, build_book


def _client(roles):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": "u1", "sub": "u1", "roles": roles, "display_name": "Кто-то"}))
    return client


@pytest.fixture()
def api_db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.models  # noqa: F401 — регистрация таблиц до create_all
    import app.v2.models  # noqa: F401
    from app.db import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.api.SessionLocal", factory)
    with factory() as db:
        build_book(db)
    return factory


def test_dictor_is_refused(api_db):
    response = _client(["dictor"]).get(f"/api/v2/books/{BOOK}/disputed")
    assert response.status_code == 403
    assert response.json()["error"] == "read_only"


def test_anonymous_is_refused(api_db):
    assert TestClient(app).get(f"/api/v2/books/{BOOK}/disputed").status_code == 401


@pytest.mark.parametrize("role", ["author", "admin"])
def test_editor_still_reads_the_queue(api_db, role):
    response = _client([role]).get(f"/api/v2/books/{BOOK}/disputed")
    assert response.status_code == 200
    assert response.json()["ok"] is True
