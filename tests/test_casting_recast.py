# tests/test_casting_recast.py
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AuthorCharacter, BookBudget, Character, DictorAssignment, Recast, User, UserRole
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


def test_a_recast_moves_forward_and_keeps_recorded_books(db):
    cycle = build_cycle(db)
    _approve(db, cycle["kuybu"][0], "Сомов Роман")
    record_take(db, cycle["books"][0], "Куйбу Дегатти", "Сомов Роман")
    out = casting.recast(db, cycle["kuybu"][1], to_actor="Иванов Иван", reason="left", comment="уехал",
                         voter_uid="u", voter_name="Белозёров", weight=2)
    db.commit()
    assert db.get(Character, "c1").actor_name == "Сомов Роман"
    assert db.get(Character, "c2").actor_name == "Иванов Иван"
    assert db.get(Character, "c3").actor_name == "Иванов Иван"
    assert db.get(AuthorCharacter, "ac-kuybu").actor_name == "Иванов Иван"
    row = db.get(Recast, out["recast_id"])
    assert (row.from_actor, row.to_actor, row.reason) == ("Сомов Роман", "Иванов Иван", "left")
    assert [b["book_id"] for b in json.loads(row.books_kept)] == ["b1"]


def test_a_recast_needs_a_known_reason_and_an_actor(db):
    cycle = build_cycle(db)
    with pytest.raises(ValueError, match="bad_reason"):
        casting.recast(db, cycle["kuybu"][0], to_actor="Иванов Иван", reason="whim", comment="",
                       voter_uid="u", voter_name="x", weight=2)
    with pytest.raises(ValueError, match="no_actor"):
        casting.recast(db, cycle["kuybu"][0], to_actor="  ", reason="left", comment="",
                       voter_uid="u", voter_name="x", weight=2)


def test_discrepancies_are_reported_not_resolved(db):
    cycle = build_cycle(db)
    cycle["kuybu"][0].actor_name = "Сомов Роман"
    cycle["kuybu"][1].actor_name = "Петров Пётр"
    db.commit()
    found = casting.cycle_discrepancies(db, "auth-1")
    assert found[0]["role_name"] == "Куйбу Дегатти"
    assert {b["actor"] for b in found[0]["books"]} == {"Сомов Роман", "Петров Пётр", ""}, "пустая книга тоже видна"
    assert db.get(Character, "c2").actor_name == "Петров Пётр", "само ничего не выбирает"


def test_assignments_follow_the_cast_and_the_narrator(db):
    cycle = build_cycle(db)
    db.add(User(id="u-p", login="tg_1", password_hash="telegram-login", display_name="Сомов Роман", is_active="true"))
    db.add(UserRole(id="r1", user_id="u-p", role="dictor"))
    db.add(BookBudget(book_id="b1", narrator_actor_name="Сомов Роман"))
    cycle["kuybu"][0].actor_name = "Сомов Роман"
    db.commit()
    assert casting.rebuild_assignments(db, "b1") == 2
    rows = {(r.character_id, r.state) for r in db.query(DictorAssignment).filter_by(user_id="u-p")}
    assert rows == {("c1", "approved"), ("", "approved")}
    cycle["kuybu"][0].actor_name = "Сомов Роман?"
    db.commit()
    casting.rebuild_assignments(db, "b1")
    assert {(r.character_id, r.state) for r in db.query(DictorAssignment).filter_by(user_id="u-p")} == {
        ("c1", "proposed"), ("", "approved")}


from fastapi.testclient import TestClient

from app.auth import session_serializer
from app.main import app


def _client(roles):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": "u1", "sub": "u1", "roles": roles, "display_name": "Кто-то"}))
    return client


@pytest.fixture()
def api_db(monkeypatch):
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.api.SessionLocal", factory)
    with factory() as session:
        build_cycle(session)
    return factory


def test_dictors_cannot_recast_or_read_discrepancies(api_db):
    client = _client(["dictor"])
    assert client.post("/api/v2/characters/c1/recast", json={"to_actor": "Иванов Иван", "reason": "left"}).status_code == 403
    assert client.get("/api/v2/authors/auth-1/cast-discrepancies").status_code == 403


def test_a_recast_writes_one_letter_for_all_changed_books(api_db, monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.role_approval.notify_role_approved",
                        lambda db, **kw: sent.append(kw) or {"notified": True})
    response = _client(["author"]).post("/api/v2/characters/c1/recast",
                                        json={"to_actor": "Иванов Иван", "reason": "left"})
    assert response.status_code == 200, response.text
    assert len(sent) == 1
    assert sent[0]["book_title"] == "Сказки волшебников 1"
    assert sent[0]["also_in"] == ["Сказки волшебников 2", "Крылья Полумрака"]


def test_a_recast_outvoted_in_a_sibling_is_not_journalled_as_changed(db):
    cycle = build_cycle(db)
    cast_role_vote(db, character=cycle["kuybu"][1], voter_uid="u-author", voter_name="Белозёров",
                   weight=2, actor_name="Сомов Роман")
    db.commit()
    out = casting.recast(db, cycle["kuybu"][0], to_actor="Иванов Иван", reason="left", comment="",
                         voter_uid="u-admin", voter_name="Владелец", weight=1)
    db.commit()
    assert [row["character_id"] for row in out["changed"]] == ["c1", "c3"]
    assert [row["character_id"] for row in out["outvoted"]] == ["c2"]
    assert [row["character_id"] for row in json.loads(db.query(Recast).one().books_changed)] == ["c1", "c3"]


def test_the_narrator_is_never_recast_cycle_wide(db):
    cycle = build_cycle(db)
    db.get(Character, "c1").name = "Рассказчик"
    db.commit()
    with pytest.raises(ValueError, match="narrator"):
        casting.recast(db, db.get(Character, "c1"), to_actor="Иванов Иван", reason="left", comment="",
                       voter_uid="u", voter_name="Белозёров", weight=2)


def test_a_recorded_book_kept_by_a_recast_is_not_a_discrepancy(db):
    cycle = build_cycle(db)
    _approve(db, cycle["kuybu"][0], "Сомов Роман")
    record_take(db, cycle["books"][0], "Куйбу Дегатти", "Сомов Роман")
    casting.recast(db, db.get(Character, "c1"), to_actor="Иванов Иван", reason="left", comment="",
                   voter_uid="u", voter_name="Белозёров", weight=2)
    db.commit()
    assert db.get(Character, "c1").actor_name == "Сомов Роман"
    assert casting.cycle_discrepancies(db, "auth-1") == []


def test_a_narrator_character_is_not_doubled_in_assignments(db):
    build_cycle(db)
    db.add(User(id="u-luk", login="luk", password_hash="x", display_name="Остапович Ян", is_active="true"))
    db.add(UserRole(user_id="u-luk", role="dictor"))
    db.get(Character, "c1").name = "Рассказчик"
    db.get(Character, "c1").actor_name = "Остапович Ян"
    db.add(BookBudget(book_id="b1", narrator_actor_name="Остапович Ян"))
    db.commit()
    casting.rebuild_assignments(db, "b1")
    rows = db.query(DictorAssignment).filter_by(book_id="b1").all()
    assert [(row.role_name, row.character_id) for row in rows] == [("Рассказчик", "")]
