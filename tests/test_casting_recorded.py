import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.services import casting
from tests.casting_cycle import build_cycle, record_take


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def test_a_role_with_a_take_is_recorded(db):
    cycle = build_cycle(db)
    record_take(db, cycle["books"][0], "Куйбу Дегатти", "Сомов Роман")
    assert casting.role_recorded(db, cycle["kuybu"][0]) is True
    assert casting.role_recorded(db, cycle["kuybu"][1]) is False


def test_siblings_are_the_same_profile_character_in_the_authors_other_books(db):
    cycle = build_cycle(db, other_author_book=True)
    siblings = casting.cycle_siblings(db, cycle["kuybu"][0])
    assert [c.id for c in siblings] == ["c2", "c3"], "чужая книга не входит в цикл"


def test_a_character_without_a_profile_has_no_siblings(db):
    cycle = build_cycle(db)
    cycle["kuybu"][0].author_character_id = ""
    db.commit()
    assert casting.cycle_siblings(db, cycle["kuybu"][0]) == []
