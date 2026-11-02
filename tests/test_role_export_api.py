"""Ручки выгрузки сценария роли: те же права и главы, что у страницы роли."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import session_serializer
from app.config import settings
from app.db import Base, SessionLocal
from app.main import app
from tests.role_export_book import BOOK, build_book

TXT = f"/api/v2/books/{BOOK}/role-script.txt"
PDF = f"/api/v2/books/{BOOK}/role-script.pdf"
TOKEN = "t" * 48


def _database(status="published"):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        build_book(session, status=status)
    return engine


@pytest.fixture()
def bind():
    old = SessionLocal.kw["bind"]

    def use(status="published"):
        SessionLocal.configure(bind=_database(status))
    yield use
    SessionLocal.configure(bind=old)


def _client(roles):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": "u1", "sub": "u1", "roles": roles, "display_name": "Роман Сомов"}))
    return client


def test_the_text_downloads_as_a_named_file(bind):
    bind()
    response = _client(["admin"]).get(TXT, params={"role": "Мира"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.content.startswith("\ufeff".encode("utf-8"))
    assert "▶ Мира: — Знаю." in response.content.decode("utf-8-sig")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;") and 'filename="TG_Mira.txt"' in disposition


def test_the_pdf_downloads_too(bind):
    bind()
    response = _client(["admin"]).get(PDF, params={"role": "Мира"})

    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"


@pytest.mark.parametrize("path", [TXT, PDF])
def test_the_usual_refusals(bind, path):
    bind()
    assert TestClient(app).get(path, params={"role": "Мира"}).status_code == 401
    assert _client(["admin"]).get(path).json()["error"] == "role_required"
    assert _client(["admin"]).get(path.replace(BOOK, "nope"), params={"role": "Мира"}).json()["error"] == "book_not_found"
    missing = _client(["admin"]).get(path, params={"role": "Никто"})
    assert missing.status_code == 404 and missing.json()["error"] == "role_has_no_lines"


def test_a_dictor_gets_nothing_from_an_unpublished_chapter(bind):
    bind(status="queued")
    response = _client(["dictor"]).get(TXT, params={"role": "Мира"})

    assert response.status_code == 404 and response.json()["error"] == "role_has_no_lines"
    assert _client(["admin"]).get(TXT, params={"role": "Мира"}).status_code == 200


def test_one_seat_keeps_it(bind, monkeypatch):
    bind()
    monkeypatch.setattr(settings, "seat_mode", "one")
    monkeypatch.setattr(settings, "seat_token", TOKEN)
    client = TestClient(app, base_url="http://127.0.0.1:51234")
    client.get(f"/seat?token={TOKEN}", follow_redirects=False)

    assert client.get(PDF, params={"role": "Мира"}).status_code == 200
