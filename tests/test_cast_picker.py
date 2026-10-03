"""План 2б: предпросмотр рекаста и список дикторов для выбора в касте (02.10)."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import dictors as dictors_api
from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import Character, DictorDemo, DictorProfile, User, UserRole
from app.services import casting
from app.services.role_votes import cast_role_vote
from tests.casting_cycle import build_cycle, record_take


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _approve(db, character, actor):
    cast_role_vote(db, character=character, voter_uid="u", voter_name="Белозёров", weight=2, actor_name=actor)
    casting.propagate_approval(db, character, voter_uid="u", voter_name="Белозёров", weight=2)
    db.commit()


def test_the_preview_says_where_the_actor_changes_and_where_he_stays_without_writing(db):
    cycle = build_cycle(db)
    _approve(db, cycle["kuybu"][0], "Сомов Роман")
    record_take(db, cycle["books"][0], "Куйбу Дегатти", "Сомов Роман")
    preview = casting.recast_preview(db, cycle["kuybu"][1], to_actor="Иванов Иван")
    assert preview["from_actor"] == "Сомов Роман"
    assert [b["book_title"] for b in preview["changed"]] == ["Сказки волшебников 2", "Крылья Полумрака"]
    assert [(b["book_title"], b["actor"]) for b in preview["kept"]] == [("Сказки волшебников 1", "Сомов Роман")]
    assert db.get(Character, "c2").actor_name == "Сомов Роман"  # ничего не записано


def test_the_preview_refuses_the_narrator_and_an_empty_actor(db):
    cycle = build_cycle(db)
    with pytest.raises(ValueError):
        casting.recast_preview(db, cycle["kuybu"][0], to_actor="")
    narrator = Character(id="n1", book_id="b1", name="Рассказчик")
    db.add(narrator)
    db.commit()
    with pytest.raises(ValueError):
        casting.recast_preview(db, narrator, to_actor="Иванов Иван")


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(dictors_api, "SessionLocal", factory)
    with factory() as session:
        session.add(User(id="u1", login="olga", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
        session.add(UserRole(user_id="u1", role="dictor"))
        session.add(DictorProfile(user_id="u1", note="тёплый тембр"))
        session.add(DictorDemo(id="d1", user_id="u1", title="сказка", stored_key="k", duration_seconds=60,
                               size_bytes=1, md5="m", source="import"))
        session.commit()
    return factory


def _client(roles):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "x", "sub": "x", "roles": roles, "display_name": "К"}))
    return client


def test_the_picker_gives_demos_and_notes_to_the_author(factory):
    item = _client(["author"]).get("/api/dictors/picker").json()["items"][0]
    assert (item["name"], item["main_demo"]["id"], item["note"], item["demos"]) == ("Ветрова Ольга", "d1", "тёплый тембр", 1)


def test_the_agent_sees_names_without_demos_or_notes(factory):
    body = _client(["agent"]).get("/api/dictors/picker").json()
    assert body["items"][0]["name"] == "Ветрова Ольга"
    assert body["items"][0]["main_demo"] is None and body["items"][0]["note"] == "" and body["can_listen"] is False


def test_a_dictor_gets_no_picker(factory):
    assert _client(["dictor"]).get("/api/dictors/picker").status_code == 403
