"""Adding a role from the reader: the name the model met but the cast never had.

The picker refuses a speaker the cast does not know, which is right — an attribution
must point at a real role — but until now the only way out was to leave the reader,
open the cast, add the role there and come back. A book has 220 roles and the ones
that are missing are found exactly while reading.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook
from app.v2.cast_ops import CreateRoleError, create_role

BOOK = "book-1"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
        session.add(Character(id="c1", book_id=BOOK, name="Дгарнин", aliases="Дгар"))
        session.commit()
        yield session


def test_a_new_role_gets_a_row_and_a_colour(db):
    result = create_role(db, book_id=BOOK, name="  Староста  ", actor_uid="u1")

    row = db.get(Character, result["character_id"])
    assert row.name == "Староста" and row.book_id == BOOK
    assert row.character_color and row.character_text_color  # a colour it can be read in
    assert result["created"] is True
    assert result["name"] == "Староста"


def test_a_name_the_cast_already_has_is_returned_not_duplicated(db):
    result = create_role(db, book_id=BOOK, name="дгарни́н", actor_uid="u1")

    assert result["created"] is False
    assert result["character_id"] == "c1"
    assert db.query(Character).filter(Character.book_id == BOOK).count() == 1


def test_an_alias_of_an_existing_role_is_the_same_role(db):
    result = create_role(db, book_id=BOOK, name="Дгар", actor_uid="u1")

    assert (result["created"], result["character_id"]) == (False, "c1")


def test_empty_and_placeholder_names_are_refused(db):
    for name in ("", "   ", "UNSURE", "unsure: кто-то из парней"):
        with pytest.raises(CreateRoleError):
            create_role(db, book_id=BOOK, name=name, actor_uid="u1")


def test_the_narrator_is_not_created_as_a_role(db):
    with pytest.raises(CreateRoleError):
        create_role(db, book_id=BOOK, name="Рассказчик", actor_uid="u1")


def test_an_unknown_book_is_refused(db):
    with pytest.raises(CreateRoleError):
        create_role(db, book_id="nope", name="Кто-то", actor_uid="u1")


def test_creating_a_role_is_written_to_the_journal(db):
    from app.models import OperatorIntervention

    create_role(db, book_id=BOOK, name="Староста", actor_uid="u1", actor_name="Оператор")
    db.commit()

    row = db.query(OperatorIntervention).filter(OperatorIntervention.action_type == "v2_create_role").one()
    assert row.book_id == BOOK
