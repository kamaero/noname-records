"""The recording screen offers the roles of a v2 book.

The workspace read its roles out of the fountain text v1 used to generate. A v2 book
has none, so `cast`, `my_roles` and the line counts all came back empty and the actor's
only option in the role field was «Другая роль…». The annotations know who speaks.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.services.recording_workspace import build_recording_workspace_context
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-1"
CH = "ch-1"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt", pipeline_mode="v2"))
        session.add(ScriptChapter(id=CH, book_id=BOOK, chapter_index=1, chapter_title="Глава 1", status="published", source_text="Текст."))
        session.add(Character(id="c1", book_id=BOOK, name="Дгарнин", actor_name="Зотов Сергей"))
        session.add(Character(id="c2", book_id=BOOK, name="Рассказчик", actor_name="Голос Автора"))
        for ordinal in range(3):
            session.add(V2Segment(id=f"{CH}:{ordinal:05d}", book_id=BOOK, chapter_id=CH, ordinal=ordinal,
                                  kind="paragraph", text="Реплика.", char_start=0, char_end=8))
        for ordinal, speaker in enumerate(("Дгарнин", "Дгарнин", "Рассказчик")):
            session.add(V2Attribution(id=str(uuid.uuid4()), segment_id=f"{CH}:{ordinal:05d}", span_start=0,
                                      span_end=8, speaker=speaker, confidence=0.9, source="llm", version=1))
        session.commit()
        yield session


def test_roles_and_their_line_counts_come_from_the_markup(db):
    context = build_recording_workspace_context(db, selected_book_id=BOOK, user_name="Зотов Сергей")

    assert context["role_line_counts"] == {"Дгарнин": 2, "Рассказчик": 1}
    assert set(context["cast_names"]) == {"Дгарнин", "Рассказчик"}


def test_the_actor_is_offered_his_own_role(db):
    context = build_recording_workspace_context(db, selected_book_id=BOOK, user_name="Зотов Сергей")

    assert [(item["name"], item["lines"]) for item in context["my_roles"]] == [("Дгарнин", 2)]


def test_an_unsure_speaker_is_not_a_role_to_record(db):
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=f"{CH}:00002", span_start=0, span_end=8,
                         speaker="UNSURE", confidence=0.0, source="llm", version=2))
    db.commit()

    context = build_recording_workspace_context(db, selected_book_id=BOOK, user_name="Зотов Сергей")

    assert "UNSURE" not in context["role_line_counts"]
