"""Описания канона из разделов энциклопедии, где имя не выделено (07.10).

Импорт пропускал записи с именем в обычном абзаце. Заполняем только пустые
описания карточек, привязанных к ролям книги.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Author, AuthorCharacter, Character, ScriptBook
from app.services.author_imports.canon_fill import fill_descriptions

FB2 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><body>'
    '<section><title><p>Северный край. Основные факты.</p></title>'
    '<p>Лира Ветер (раньше - Мастер). Хранительница маяка. Любит море.</p>'
    '<p>Лирель. Совсем другой человек.</p>'
    '</section>'
    '<section><title><p>Северный край. Мироустройство.</p></title>'
    '<p>Орден. Воин.</p>'
    '<p>Каян — упоминание в середине, не запись.</p>'
    '<p>Тор Рин. Самый северный остров.</p>'
    '<p>Тор-Рин. Самый северный остров.</p>'
    '</section></body></FictionBook>'
).encode("utf-8")


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(Author(id="a1", name="Автор Примера", slug="example"))
        session.add(ScriptBook(id="b1", title="Путешествие к маяку", source_filename="k", source_format="docx",
                               author_id="a1"))
        cards = [("asa", "Лира Ветер", ""), ("goh", "Орден", ""), ("kag", "Каян", ""), ("hor", "Тор", ""),
                 ("full", "Лирель", "Уже есть описание."), ("free", "Лирель2", "")]
        for cid, name, description in cards:
            session.add(AuthorCharacter(id=cid, author_id="a1", canonical_name=name, aliases="[]",
                                        description=description, source_topic="", status="confirmed"))
        for cid in ("asa", "goh", "kag", "full", "hor"):
            session.add(Character(id=f"r-{cid}", book_id="b1", name=cid, author_character_id=cid))
        session.commit()
        yield session


def test_a_plain_paragraph_fills_an_empty_canon_card(db):
    report = fill_descriptions(db, "a1", FB2, book_id="b1", apply=True)
    asa = db.get(AuthorCharacter, "asa")
    assert asa.description == "(раньше - Мастер). Хранительница маяка. Любит море."
    assert asa.source_topic == "Северный край. Основные факты."
    assert db.get(AuthorCharacter, "goh").description == "Воин."
    assert sorted(report["filled"]) == ["Лира Ветер", "Орден"]


def test_a_name_inside_a_sentence_is_not_an_entry(db):
    fill_descriptions(db, "a1", FB2, book_id="b1", apply=True)
    assert db.get(AuthorCharacter, "kag").description == ""


def test_filled_cards_and_cards_without_roles_are_left_alone(db):
    fill_descriptions(db, "a1", FB2, book_id="b1", apply=True)
    assert db.get(AuthorCharacter, "full").description == "Уже есть описание."
    assert db.get(AuthorCharacter, "free").description == ""        # не привязана к роли книги


def test_dry_run_writes_nothing(db):
    report = fill_descriptions(db, "a1", FB2, book_id="b1", apply=False)
    assert report["filled"] and db.get(AuthorCharacter, "asa").description == ""


def test_a_longer_name_starting_with_the_role_is_not_its_entry(db):
    """Дефис без пробела обозначает составное имя, а не отдельную роль."""
    fill_descriptions(db, "a1", FB2, book_id="b1", apply=True)
    assert db.get(AuthorCharacter, "hor").description == ""
