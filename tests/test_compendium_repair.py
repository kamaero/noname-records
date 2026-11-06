"""Починка карточек канона, испорченных июньским разбором энциклопедии (06.10).

Перезаписываем только то, что лежит в базе ровно в испорченном виде: карточку, которую
потом правили руками, не трогаем.
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Author, AuthorCharacter, BookIllustration, Character
from app.services.author_imports.compendium_repair import repair

TOPIC = "Сесавур, Мистерия. Лауреаты премии Бриара."
FB2 = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><body>'
    f'<section><title><p>{TOPIC}</p></title>'
    '<p>1513. <strong>Ныкфо Пудисди</strong>. Волшебник из старой семьи.</p>'
    '<p>-32. <strong>Ныкфо П</strong><strong>эыхялсы</strong>. Родился в 204 году.</p>'
    '<p>17. <strong>Удхотс</strong><strong> Конх</strong>. Изобретатель.</p>'
    '<p>18. <strong>Сюдиб</strong><strong>ан</strong>. Алхимик.</p>'
    '<p>19. <strong>Лиорн</strong>. Правлено руками.</p>'
    '<p><strong>Ровный</strong>. Без номера — не испорчен.</p>'
    '<p>20. <strong>Дямбу</strong>. Он же Молчальник. Бледный человек.</p>'
    '</section></body></FictionBook>'
).encode("utf-8")


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(Author(id="a1", name="Вискон Варени", slug="vareni"))

        def card(cid, name, description, topic=TOPIC):
            session.add(AuthorCharacter(id=cid, author_id="a1", canonical_name=name, aliases="[]",
                                        description=description, source_topic=topic, status="confirmed"))

        card("deg", "Ныкфо Пудисди", "удисди. Волшебник из старой семьи.")       # испорчено разбором
        card("gtr", "Ныкфо П", "кфо Пэыхялсы. Родился в 204 году.")              # обрезанное имя
        card("gfull", "Ныкфо Пэыхялсы", "", topic="")                            # полная, без описания
        card("amb", "Удхотс", "хотс Конх. Изобретатель.")               # обрезано, полной нет
        card("zod", "Сюдиб", "юдибан. Алхимик.")                          # обрезано, но на ней роль
        card("lio", "Лиорн", "Описание, которое поправили руками.")              # не трогать
        card("rov", "Ровный", "Без номера — не испорчен.")
        card("sit", "Дямбу", "ямбу. Он же Молчальник. Бледный человек.")       # псевдоним потерян
        session.add(Character(id="ch1", book_id="b1", name="Сюдибан", author_character_id="zod"))
        session.commit()
        yield session


def test_dry_run_changes_nothing(db):
    report = repair(db, "a1", FB2, apply=False)
    assert report["descriptions"] and db.get(AuthorCharacter, "deg").description.startswith("удисди")


def test_damaged_description_is_restored(db):
    repair(db, "a1", FB2, apply=True)
    assert db.get(AuthorCharacter, "deg").description == "Волшебник из старой семьи."
    assert db.get(AuthorCharacter, "rov").description == "Без номера — не испорчен."


def test_hand_edited_card_is_left_alone(db):
    report = repair(db, "a1", FB2, apply=True)
    assert db.get(AuthorCharacter, "lio").description == "Описание, которое поправили руками."
    assert "Лиорн" in report["skipped_edited"]


def test_truncated_name_merges_into_the_full_card_when_it_exists(db):
    repair(db, "a1", FB2, apply=True)
    assert db.get(AuthorCharacter, "gtr") is None
    full = db.get(AuthorCharacter, "gfull")
    assert full.description == "Родился в 204 году." and full.source_topic == TOPIC


def test_truncated_name_is_renamed_when_there_is_no_full_card(db):
    repair(db, "a1", FB2, apply=True)
    amb = db.get(AuthorCharacter, "amb")
    assert amb.canonical_name == "Удхотс Конх" and amb.description == "Изобретатель."


def test_a_referenced_truncated_card_is_renamed_not_deleted(db):
    db.add(AuthorCharacter(id="zfull", author_id="a1", canonical_name="Сюдибан", aliases="[]", description="",
                           source_topic="", status="confirmed"))
    db.commit()
    report = repair(db, "a1", FB2, apply=True)
    assert db.get(AuthorCharacter, "zod") is not None          # на неё ссылается роль — не удаляем
    assert "Сюдиб" in report["kept_referenced"]



def test_a_lost_alias_comes_back(db):
    """«ри. Он же Молчальник…» — старый разбор не узнал псевдоним за обрезком."""
    repair(db, "a1", FB2, apply=True)
    assert json.loads(db.get(AuthorCharacter, "sit").aliases) == ["Молчальник"]
