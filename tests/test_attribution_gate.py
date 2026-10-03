"""Гейт честности: слой 1 не смеет доказывать ярлык, который человек исправил."""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.pipeline.attribution_gate import check_gate, render_gate
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(ScriptBook(id="b1", title="Крылья полумрака", source_filename="k.txt",
                               source_format="txt", created_at=utcnow_naive()))
        session.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=1,
                                  chapter_title="Глава 1", status="published"))
        session.add(Character(id="c1", book_id="b1", name="Дгарнин", aliases="",
                              appears_in="1", created_at=utcnow_naive()))
        session.add(Character(id="c2", book_id="b1", name="Гамук", aliases="",
                              appears_in="1", created_at=utcnow_naive()))
        session.commit()
        yield session


def _segment(db, ordinal, text):
    segment_id = f"ch1:{ordinal:05d}"
    db.add(V2Segment(id=segment_id, book_id="b1", chapter_id="ch1", ordinal=ordinal,
                     kind="paragraph", text=text))
    return segment_id


def _span(db, segment_id, version, speaker, start, end, source="llm"):
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, version=version,
                         speaker=speaker, span_start=start, span_end=end, source=source,
                         created_at=utcnow_naive()))


TEXT = "- Привет, - сказал Дгарнин."


def test_a_correction_the_layer_still_proves_is_a_violation(db):
    """Оператор отдал реплику Гамуку, а ремарка «сказал Дгарнин» доказывает старое."""
    segment_id = _segment(db, 0, TEXT)
    _span(db, segment_id, 1, "Дгарнин", 0, 10)
    _span(db, segment_id, 1, "Рассказчик", 10, 27)
    _span(db, segment_id, 2, "Гамук", 0, 10, source="operator")
    _span(db, segment_id, 2, "Рассказчик", 10, 27, source="operator")
    db.commit()

    result = check_gate(db, book_id="b1")

    assert result["checked"] == 1
    assert [v.kind for v in result["violations"]] == ["cue_named"]
    assert result["violations"][0].old_speaker == "Дгарнин"
    assert result["violations"][0].new_speakers == ["Гамук"]
    assert "Дгарнин" in result["violations"][0].quote
    assert result["to_other_character"] == 1


def test_a_correction_without_any_proof_is_not_a_violation(db):
    """Голая реплика без ремарки: слой 1 её не доказывал, исправлять никому не мешал."""
    segment_id = _segment(db, 0, "- Привет.")
    _span(db, segment_id, 1, "Дгарнин", 0, 9)
    _span(db, segment_id, 2, "Гамук", 0, 9, source="operator")
    db.commit()

    result = check_gate(db, book_id="b1")

    assert result["checked"] == 1
    assert result["violations"] == []


def test_a_correction_that_only_moved_the_border_is_not_counted(db):
    """Оператор отрезал ремарку от реплики, персонаж тот же — это не смена ярлыка."""
    segment_id = _segment(db, 0, TEXT)
    _span(db, segment_id, 1, "Дгарнин", 0, 27)
    _span(db, segment_id, 2, "Дгарнин", 0, 10, source="operator")
    _span(db, segment_id, 2, "Рассказчик", 10, 27, source="operator")
    db.commit()

    result = check_gate(db, book_id="b1")

    assert result["checked"] == 0
    assert result["violations"] == []


def test_versions_written_by_the_model_are_not_treated_as_answers(db):
    """Известный ответ даёт только человек: правка модели гейту не указ."""
    segment_id = _segment(db, 0, TEXT)
    _span(db, segment_id, 1, "Дгарнин", 0, 10)
    _span(db, segment_id, 1, "Рассказчик", 10, 27)
    _span(db, segment_id, 2, "Гамук", 0, 10, source="llm_review")
    _span(db, segment_id, 2, "Рассказчик", 10, 27, source="llm_review")
    db.commit()

    result = check_gate(db, book_id="b1")

    assert result["checked"] == 0


def test_the_summary_states_the_verdict_in_words(db):
    segment_id = _segment(db, 0, TEXT)
    _span(db, segment_id, 1, "Дгарнин", 0, 10)
    _span(db, segment_id, 1, "Рассказчик", 10, 27)
    _span(db, segment_id, 2, "Гамук", 0, 10, source="operator")
    _span(db, segment_id, 2, "Рассказчик", 10, 27, source="operator")
    db.commit()

    text = render_gate(check_gate(db, book_id="b1"))

    assert "оператор сменил персонажа" in text
    assert "cue_named" in text
