"""Binding a book to its author — the step that makes the cast fill itself.

«Крылья полумрака» came in with a title that names no author, so the ingest guess
found nothing, the book stayed unbound, and the profile with 921 cast decisions had
no way to reach it. Two things close that: the profile screen offers the author whose
roster the book's own roles match, and binding applies the profile straight away.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Author, AuthorCharacter, Character, ScriptBook
from app.v2.profile_ops import bind_author, book_profile

BOOK = "book-1"
AUTHOR = "author-1"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья полумрака", source_filename="k.docx", source_format="docx"))
        session.add(Author(id=AUTHOR, name="Александр Белозёров", slug="belozerov"))
        session.add(Author(id="author-2", name="Другой Автор", slug="other"))
        for name, actor in (("Дгарнин", "Зотов Сергей"), ("Пупип", "Литвиненко Константин"), ("Гамук", "")):
            session.add(AuthorCharacter(
                id=f"ac-{name}", author_id=AUTHOR, canonical_name=name, actor_name=actor, status="confirmed",
            ))
        session.add(AuthorCharacter(id="ac-x", author_id="author-2", canonical_name="Кто-то", status="confirmed"))
        for name in ("Дгарнин", "Пупип", "Гамук", "Абба Сегорон"):
            session.add(Character(id=f"c-{name}", book_id=BOOK, name=name))
        session.commit()
        yield session


def test_an_unbound_book_is_offered_the_author_its_roles_belong_to(db):
    profile = book_profile(db, BOOK)

    assert profile["author"] is None
    assert profile["suggested"]["author_id"] == AUTHOR
    assert profile["suggested"]["matched"] == 3
    assert [item["id"] for item in profile["authors"]] == [AUTHOR, "author-2"]


def test_binding_applies_the_profile_at_once(db):
    result = bind_author(db, book=db.get(ScriptBook, BOOK), author_id=AUTHOR, actor_uid="u1")
    db.commit()

    assert result["author_id"] == AUTHOR
    assert result["applied"]["actors_applied"] == 2
    assert db.get(Character, "c-Дгарнин").actor_name == "Зотов Сергей"
    assert db.get(ScriptBook, BOOK).author_id == AUTHOR


def test_a_bound_book_is_not_offered_a_suggestion(db):
    bind_author(db, book=db.get(ScriptBook, BOOK), author_id=AUTHOR, actor_uid="u1")
    db.commit()

    profile = book_profile(db, BOOK)

    assert profile["suggested"] is None
    assert profile["author"]["name"] == "Александр Белозёров"


def test_unbinding_is_possible_and_changes_nothing_else(db):
    bind_author(db, book=db.get(ScriptBook, BOOK), author_id=AUTHOR, actor_uid="u1")
    db.commit()

    result = bind_author(db, book=db.get(ScriptBook, BOOK), author_id="", actor_uid="u1")
    db.commit()

    assert result["author_id"] == ""
    assert result["applied"] is None
    assert db.get(ScriptBook, BOOK).author_id == ""
    # the actors that were already applied stay where they are
    assert db.get(Character, "c-Дгарнин").actor_name == "Зотов Сергей"


def test_an_unknown_author_is_refused(db):
    with pytest.raises(ValueError):
        bind_author(db, book=db.get(ScriptBook, BOOK), author_id="nope", actor_uid="u1")
