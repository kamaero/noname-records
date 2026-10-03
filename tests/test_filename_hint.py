"""Форма показывает диктору точное имя, которым назвать файл.

«Назовите как-нибудь так» оставляет варианты, а вариантов быть не должно: имя строится
из книги, главы, роли и актёра — всё это форма уже знает, — значит она может показать
не образец, а именно то имя, которое ждут от этого файла.

Считает его сервер, а не браузер: транслитерация живёт в одном месте, и второй её
экземпляр на другом языке разошёлся бы с первым в первый же месяц.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.auth import session_serializer
from app.db import Base, SessionLocal, engine as app_engine
from app.main import app
from app.models import Character, ScriptBook
from app.time_utils import utcnow_naive


@pytest.fixture(autouse=True)
def _database():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal.configure(bind=engine)
    # Зотов утверждён на Дгарнина — от этого и зависит, дубль это или проба.
    with SessionLocal() as db:
        book = ScriptBook(title="Крылья полумрака", source_filename="k.txt", source_format="txt", created_at=utcnow_naive())
        db.add(book)
        db.flush()
        db.add(Character(book_id=book.id, name="Дгарнин", actor_name="Зотов"))
        db.commit()
    try:
        yield
    finally:
        SessionLocal.configure(bind=app_engine)


def _client(roles=("dictor",)):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "u1", "sub": "u1", "roles": list(roles), "display_name": "Зотов"}))
    return client


def _hint(**params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return _client().get(f"/api/recording/filename?{query}")


def test_a_stranger_gets_nothing():
    assert TestClient(app).get("/api/recording/filename").status_code == 401


def test_the_name_for_an_approved_role():
    body = _hint(book_code="КП", chapter="Глава 1. Ничего особенного", role="Дгарнин", actor_name="Зотов").json()

    assert body["filename"] == "KP_Ch01_Dgarnin_Zotov.wav"
    assert body["kind"] == "take"


def test_the_name_for_an_audition():
    """Роль не за ней — значит проба, о чём бы форма ни спрашивала."""
    body = _hint(book_code="КП", chapter="Глава 1", role="Оснива", actor_name="Натали Голубева").json()

    assert body["filename"] == "KP_Ch01_Osniva_NataliGolubeva_proba.wav"
    assert body["kind"] == "audition"


def test_an_audition_read_from_nowhere_in_particular():
    body = _hint(book_code="КП", role="Оснива", actor_name="Натали Голубева").json()

    assert body["filename"] == "KP_Osniva_NataliGolubeva_proba.wav"


def test_the_dictor_is_not_asked_which_it_is():
    """Он присылает чужую роль, назвав её дублем; имя всё равно выходит пробой."""
    body = _hint(book_code="КП", chapter="Глава 1", role="Сатухух", actor_name="Зотов", kind="take").json()

    assert body["kind"] == "audition"
    assert body["filename"].endswith("_proba.wav")


def test_without_a_role_there_is_nothing_to_show():
    """Половина имени — хуже, чем ничего: диктор скопирует её и решит, что так и надо."""
    body = _hint(book_code="КП", chapter="Глава 1", actor_name="Зотов").json()

    assert body["filename"] == ""
