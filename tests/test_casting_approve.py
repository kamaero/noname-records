# tests/test_casting_approve.py
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AuthorCharacter, Character
from app.services import casting
from app.services.role_votes import cast_role_vote
from tests.casting_cycle import build_cycle, record_take


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _approve(db, character, actor, *, weight=2):
    cast_role_vote(db, character=character, voter_uid="u-author", voter_name="Белозёров",
                   weight=weight, actor_name=actor)
    return casting.propagate_approval(db, character, voter_uid="u-author", voter_name="Белозёров", weight=weight)


def test_an_approved_actor_takes_the_role_in_every_book_of_the_cycle(db):
    cycle = build_cycle(db)
    result = _approve(db, cycle["kuybu"][0], "Сомов Роман")
    db.commit()
    assert [db.get(Character, c.id).actor_name for c in cycle["kuybu"]] == ["Сомов Роман"] * 3
    assert db.get(AuthorCharacter, "ac-kuybu").actor_name == "Сомов Роман"
    assert [row["character_id"] for row in result["changed"]] == ["c2", "c3"]


def test_a_proposal_stays_in_its_own_book(db):
    cycle = build_cycle(db)
    result = _approve(db, cycle["kuybu"][0], "Сомов Роман?")
    db.commit()
    assert db.get(Character, "c2").actor_name == ""
    assert db.get(AuthorCharacter, "ac-kuybu").actor_name == ""
    assert result["changed"] == []


def test_a_book_where_the_role_is_recorded_keeps_its_actor(db):
    cycle = build_cycle(db)
    _approve(db, cycle["kuybu"][0], "Сомов Роман")
    db.commit()
    record_take(db, cycle["books"][2], "Куйбу Дегатти", "Сомов Роман")
    result = _approve(db, cycle["kuybu"][1], "Иванов Иван")
    db.commit()
    assert db.get(Character, "c3").actor_name == "Сомов Роман", "записано — не трогаем"
    assert db.get(Character, "c1").actor_name == "Иванов Иван"
    assert [row["character_id"] for row in result["kept"]] == ["c3"]


def test_the_same_actor_again_changes_nothing_elsewhere(db):
    cycle = build_cycle(db)
    _approve(db, cycle["kuybu"][0], "Сомов Роман")
    db.commit()
    result = _approve(db, cycle["kuybu"][0], "Сомов Роман")
    assert result["changed"] == []


def test_a_book_of_another_author_is_never_touched(db):
    cycle = build_cycle(db, other_author_book=True)
    _approve(db, cycle["kuybu"][0], "Сомов Роман")
    db.commit()
    assert db.get(Character, "c-other").actor_name == ""


def test_a_sibling_where_the_author_outvotes_the_approval_is_not_reported_changed(db):
    cycle = build_cycle(db)
    cast_role_vote(db, character=cycle["kuybu"][1], voter_uid="u-author", voter_name="Белозёров",
                   weight=2, actor_name="Сомов Роман")
    cast_role_vote(db, character=cycle["kuybu"][0], voter_uid="u-admin", voter_name="Владелец",
                   weight=1, actor_name="Иванов Иван")
    result = casting.propagate_approval(db, cycle["kuybu"][0], voter_uid="u-admin", voter_name="Владелец", weight=1)
    db.commit()
    assert db.get(Character, "c2").actor_name == "Сомов Роман"
    assert [row["character_id"] for row in result["changed"]] == ["c3"]
    assert [(row["character_id"], row["actor"]) for row in result["outvoted"]] == [("c2", "Сомов Роман")]


def test_a_narrator_linked_to_a_profile_never_goes_cycle_wide(db):
    cycle = build_cycle(db)
    for character in cycle["kuybu"]:
        db.get(Character, character.id).name = "Рассказчик"
    db.commit()
    result = _approve(db, cycle["kuybu"][0], "Остапович Ян")
    db.commit()
    assert result["changed"] == [] and db.get(Character, "c2").actor_name == ""
    assert db.get(AuthorCharacter, "ac-kuybu").actor_name == ""
