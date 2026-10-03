"""Проба или дубль — решает система, а не галочка в форме.

Галочку дикторы ставили неверно, и это не их вина: они не обязаны помнить, на какие
роли утверждены. Система обязана — она же их и утверждала.

Правило одно: диктор записывает роль, которая за ним, — это дубль. Записывает любую
другую — это проба, что бы он ни выбрал в форме. Роль считается его и тогда, когда он
читал этого персонажа в другой книге: голос за персонажем, а не за книгой.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook
from app.services.upload_kind import classify_upload_kind
from app.time_utils import utcnow_naive


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        book = ScriptBook(title="Крылья полумрака", source_filename="k.txt", source_format="txt", created_at=utcnow_naive())
        other = ScriptBook(title="Сказки волшебников", source_filename="s.txt", source_format="txt", created_at=utcnow_naive())
        session.add_all([book, other])
        session.flush()
        session.add_all([
            Character(book_id=book.id, name="Сатухух", actor_name="Сомов Роман"),
            Character(book_id=book.id, name="Куйбу Дегатти", actor_name="Сомов Роман"),
            Character(book_id=book.id, name="Дгарнин", actor_name=""),
            Character(book_id=other.id, name="Бдеукс", actor_name="Зотов Сергей"),
        ])
        session.flush()
        yield session


def test_his_own_role_is_a_take(db):
    assert classify_upload_kind(db, role="Сатухух", actor_name="Роман Сомов") == "take"


def test_somebody_elses_role_is_an_audition(db):
    assert classify_upload_kind(db, role="Сатухух", actor_name="Сергей Зотов") == "audition"


def test_a_role_nobody_holds_is_an_audition(db):
    assert classify_upload_kind(db, role="Дгарнин", actor_name="Роман Сомов") == "audition"


def test_a_role_from_another_book_still_counts_as_his(db):
    """Голос закреплён за персонажем, а не за книгой: читал Бдеукса там — читает и здесь."""
    assert classify_upload_kind(db, role="Бдеукс", actor_name="Сергей Зотов") == "take"


def test_the_name_is_matched_the_way_the_cast_matches_names(db):
    """«Сомов Роман» в касте и «Роман Сомов» в учётке — один человек."""
    assert classify_upload_kind(db, role="Куйбу Дегатти", actor_name="Сомов Роман") == "take"


def test_a_role_the_book_never_heard_of_is_an_audition(db):
    assert classify_upload_kind(db, role="Кто-То Ещё", actor_name="Роман Сомов") == "audition"


def test_stress_marks_and_case_do_not_make_it_a_new_role(db):
    assert classify_upload_kind(db, role="сату́хух", actor_name="Роман Сомов") == "take"


def test_without_a_name_there_is_nothing_to_approve(db):
    assert classify_upload_kind(db, role="Сатухух", actor_name="") == "audition"


def test_a_merely_suggested_actor_brings_an_audition_not_a_take(db):
    """«Дгарнин — Натали Ким?» — предложение агента, а не утверждение.

    Ради этого случая роль и заведена: агент предлагает актрису, приносит её пробу,
    владелец слушает и решает. Прочтись «Натали Ким?» как утверждённая, запись стала
    бы дублем — а дубль требует главы, которой у пробы нет: `app/api/recording.py`
    отвечает `chapter_required`, и проба не доходит вовсе. С главой было бы хуже:
    она легла бы дублем и не показалась в листе проб.
    """
    db.add(Character(book_id=db.query(ScriptBook).first().id, name="Химера", actor_name="Натали Ким?"))
    db.flush()

    assert classify_upload_kind(db, role="Химера", actor_name="Натали Ким") == "audition"


def test_the_same_name_without_the_mark_is_a_take(db):
    """Проверка на предварительность, а не на имя: утвердили — и та же запись дубль."""
    db.add(Character(book_id=db.query(ScriptBook).first().id, name="Химера", actor_name="Натали Ким"))
    db.flush()

    assert classify_upload_kind(db, role="Химера", actor_name="Натали Ким") == "take"
