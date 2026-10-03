"""Предпросмотр загрузки должен открываться тому, кто грузит.

Он проверял роль `dictor_pro` — ту, которой не было ни у одной учётки. Предпросмотр
отвечал 403 всем пятидесяти девяти дикторам, и молча: панель считает его подсказкой и
глотает ошибку. Диктор видел пустоту там, где сервер обещал показать, чем станет его
файл. Роль с тех пор упразднена совсем.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.auth import session_serializer
from app.db import Base, SessionLocal, engine as app_engine
from app.main import app


@pytest.fixture(autouse=True)
def _database():
    """Предпросмотр сверяется с уже загруженным, значит ему нужна таблица.

    Один и тот же in-memory на все соединения: маршрут исполняется в другом потоке, и
    обычный пул выдал бы ему пустую базу. Сессия-фабрика перенастраивается, а не
    подменяется, — обработчик держит именно её, а не имя модуля.
    """
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal.configure(bind=engine)
    try:
        yield
    finally:
        SessionLocal.configure(bind=app_engine)


def _client(roles):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "u1", "sub": "u1", "roles": roles, "display_name": "Роман Сомов"}))
    return client


def _preview(roles):
    return _client(roles).post("/dictor-pro/batch-validate", json={
        "book_code": "КП", "actor_name": "Роман Сомов",
        "files": [{"name": "03-Za'Maor.wav", "size": 10}],
        "overrides": [{"book_code": "КП", "chapter": "Глава3", "role": "Сатухух", "actor_name": "Роман Сомов"}],
    })


def test_a_stranger_still_gets_nothing():
    assert TestClient(app).post("/dictor-pro/batch-validate", json={"files": []}).status_code == 401


def test_the_dictor_who_uploads_sees_the_preview():
    assert _preview(["dictor"]).status_code == 200


def test_the_owner_and_the_author_see_it_too():
    assert _preview(["admin"]).status_code == 200
    assert _preview(["author"]).status_code == 200


def test_the_preview_warns_when_the_file_names_another_role():
    """Роман выбрал «Сатухух» и прислал 03-Za'Maor.wav — до этого никто не сверял."""
    item = _preview(["dictor"]).json()["items"][0]

    assert "role_name_mismatch" in item["warnings"]
    assert item["ok"] is True


def test_an_audition_preview_asks_for_no_chapter():
    response = _client(["dictor"]).post("/dictor-pro/batch-validate", json={
        "book_code": "КП", "actor_name": "София Ершова",
        "files": [{"name": "Энея_пробы.wav", "size": 10}],
        "overrides": [{"book_code": "КП", "kind": "audition", "role": "Эней", "actor_name": "София Ершова"}],
    })
    item = response.json()["items"][0]

    assert item["ok"] is True
    assert item["canonical_filename"] == "KP_Eney_SofiyaErshova_proba.wav"
