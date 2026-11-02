"""Характеристика роли по тексту книги (07.10): кому пишем, что даём модели, что пишем."""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import AuthorCharacter, Character, ScriptBook, ScriptChapter
from app.services import role_profiles
from app.v2.models import V2Attribution, V2Segment

BOOK = "b1"


@pytest.fixture()
def db():
    # одно соединение на всех: тест правки «во время вызова» открывает вторую сессию
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as s:
        s.add(ScriptBook(id=BOOK, title="Карта", display_title="Карта Приозерья", source_filename="k",
                         source_format="txt"))
        s.add(ScriptChapter(id="ch1", book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
        for i in range(8):
            text = f"Абзац {i}."
            s.add(V2Segment(id=f"ch1:{i:05d}", book_id=BOOK, chapter_id="ch1", ordinal=i, kind="paragraph",
                            text=text, char_start=0, char_end=len(text)))
        s.add(AuthorCharacter(id="canon-full", author_id="a", canonical_name="Ныкфо", aliases="[]",
                              description="Волшебник.", source_topic="", status="confirmed"))
        s.add(AuthorCharacter(id="canon-empty", author_id="a", canonical_name="Емеммо", aliases="[]",
                              description="", source_topic="", status="confirmed"))
        roles = [
            Character(id="asa", book_id=BOOK, name="Емеммо", race="человек", author_character_id="canon-empty"),
            Character(id="main", book_id=BOOK, name="Ныкфо", author_character_id="canon-full"),
            Character(id="temp", book_id=BOOK, name="Алппис", temperament="Немногословный вождь."),
            Character(id="nar", book_id=BOOK, name="Рассказчик"),
            Character(id="mute", book_id=BOOK, name="Немой"),
            Character(id="done", book_id=BOOK, name="Готовый", text_profile="Уже есть.", text_profile_by="human"),
        ]
        s.add_all(roles)
        for seg, who in (("ch1:00003", "Емеммо"), ("ch1:00006", "Емеммо"), ("ch1:00001", "Ныкфо"),
                         ("ch1:00002", "Рассказчик"), ("ch1:00004", "Готовый")):
            s.add(V2Attribution(id=str(uuid.uuid4()), segment_id=seg, span_start=0, span_end=8,
                                speaker=who, confidence=0.9, source="llm", version=1))
        s.commit()
        yield s


def test_only_roles_nobody_described_are_candidates(db):
    names = [r.name for r in role_profiles.candidates(db, BOOK)]
    # канон с описанием, темперамент, рассказчик, готовый черновик — мимо; без реплик — в списке,
    # но модель не позовётся (см. ниже)
    assert names == ["Емеммо", "Немой"]


def test_the_prompt_marks_the_roles_paragraphs_and_keeps_context(db):
    prompt, paragraphs = role_profiles.role_prompt(db, BOOK, db.get(Character, "asa"))
    assert "Роль: Емеммо" in prompt and "Раса: человек" in prompt and "Карта Приозерья" in prompt
    assert "▶ Абзац 3." in prompt and "▶ Абзац 6." in prompt
    assert "\nАбзац 2." in prompt and "\nАбзац 7." in prompt     # соседи по одному с каждой стороны
    assert paragraphs == 6


def test_estimate_counts_only_roles_with_lines(db):
    est = role_profiles.estimate(db, BOOK)
    assert est["roles"] == 1 and est["names"] == ["Емеммо"] and est["paragraphs"] == 6
    assert est["estimate_rub"] > 0


def test_run_writes_a_model_draft_and_skips_silent_text(db):
    calls = []

    def ask(model, system, user, schema):
        calls.append(user)
        return {"profile": "Адмирал флота,  любит море.", "grounded": True}

    report = role_profiles.run(db, BOOK, ask=ask, log=lambda _m: None)
    row = db.get(Character, "asa")
    assert (row.text_profile, row.text_profile_by) == ("Адмирал флота, любит море.", "model")
    assert report["done"] == ["Емеммо"] and len(calls) == 1      # «Немой» без реплик — модель не звали


def test_an_ungrounded_answer_leaves_the_field_empty(db):
    report = role_profiles.run(db, BOOK, ask=lambda *a: {"profile": "Что-то.", "grounded": False},
                               log=lambda _m: None)
    assert db.get(Character, "asa").text_profile == "" and report["empty"] == ["Емеммо"]


def test_a_failed_call_does_not_stop_the_run(db):
    def ask(*_a):
        raise RuntimeError("503")

    report = role_profiles.run(db, BOOK, ask=ask, log=lambda _m: None)
    assert report["failed"] == ["Емеммо"] and db.get(Character, "asa").text_profile == ""


def test_a_hand_edit_made_during_the_call_wins(db):
    def ask(*_a):
        other = sessionmaker(bind=db.get_bind())()
        other.get(Character, "asa").temperament = "Вписал человек."
        other.commit()
        return {"profile": "Модель.", "grounded": True}

    report = role_profiles.run(db, BOOK, ask=ask, log=lambda _m: None)
    assert report["failed"] == [] and db.get(Character, "asa").text_profile == ""
