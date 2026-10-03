"""An operator reassigns a segment; the newest version is theirs and the journal knows."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, OperatorIntervention, ScriptBook, ScriptChapter
from app.v2.attribution_ops import ReassignError, normalise_spans, reassign_segment
from app.v2.models import V2Attribution, V2Segment
from app.v2.reader import effective_attributions


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    db.add(ScriptBook(id="b1", title="B", status="author_review", source_filename="b.txt", source_format="txt"))
    db.add(ScriptChapter(id="c1", book_id="b1", chapter_index=1, chapter_title="1"))
    db.add(Character(id="ch1", book_id="b1", char_map_id="m1", name="Пупип", aliases="Заг"))
    text = "Он вздохнул. — Идём, — и пошёл дальше."
    db.add(V2Segment(id="c1:00000", book_id="b1", chapter_id="c1", ordinal=0, text=text, char_end=len(text)))
    db.add(V2Attribution(id="a1", segment_id="c1:00000", span_start=0, span_end=len(text), speaker="Рассказчик", confidence=0.9, source="llm", version=1))
    db.commit()
    return text


def test_reassign_writes_a_new_operator_version_and_a_journal_row():
    SessionLocal = _session()
    with SessionLocal() as db:
        text = _seed(db)
        result = reassign_segment(db, segment_id="c1:00000", actor_uid="u1", actor_name="оператор", spans=[
            {"start": 0, "end": 12, "speaker": "Рассказчик"},
            {"start": 13, "end": 22, "speaker": "заг"},
            {"start": 23, "end": len(text), "speaker": "Рассказчик"},
        ])
        db.commit()
        assert result["spans"] == 3
        rows = effective_attributions(db, ["c1:00000"])["c1:00000"]
        assert [r.speaker for r in rows] == ["Рассказчик", "Пупип", "Рассказчик"]
        assert {r.source for r in rows} == {"operator"} and {r.version for r in rows} == {2}
        journal = db.query(OperatorIntervention).one()
        assert journal.action_type == "v2_reassign_speaker"


@pytest.mark.parametrize("spans,code", [
    ([], "spans_required"),
    ([{"start": 0, "end": 5, "speaker": ""}], "speaker_required"),
    ([{"start": 0, "end": 10, "speaker": "Рассказчик"}, {"start": 5, "end": 12, "speaker": "Пупип"}], "spans_overlap"),
    ([{"start": 0, "end": 5, "speaker": "Никто"}], "unknown_speaker"),
    ([{"start": 5, "end": 5, "speaker": "Пупип"}], "empty_span"),
])
def test_reassign_rejects_bad_input(spans, code):
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        with pytest.raises(ReassignError) as exc:
            reassign_segment(db, segment_id="c1:00000", spans=spans, actor_uid="u1")
        assert exc.value.code == code


def test_normalise_clips_to_the_text():
    spans = normalise_spans([{"start": -3, "end": 999, "speaker": "X"}], text_length=10)
    assert spans == [(0, 10, "X", 1.0)]


def test_reassign_keeps_the_source_of_spans_it_was_told_to_keep():
    """Соседей, чью роль никто не менял, нельзя метить правкой человека.

    Слой доказательств не трогает исправленное оператором (гейт честности); ложная
    метка тихо выводит отрезок из-под проверки. Источник сохраняет только серверный
    код — аргументом, а не полем в теле запроса.
    """
    SessionLocal = _session()
    with SessionLocal() as db:
        text = _seed(db)
        reassign_segment(db, segment_id="c1:00000", actor_uid="u1", spans=[
            {"start": 0, "end": 12, "speaker": "Рассказчик", "confidence": 0.9},
            {"start": 13, "end": 22, "speaker": "Пупип"},
            {"start": 23, "end": len(text), "speaker": "Рассказчик", "confidence": 0.9},
        ], keep_source={(0, 12): "llm", (23, len(text)): "llm"})
        db.commit()
        rows = effective_attributions(db, ["c1:00000"])["c1:00000"]
        assert [(r.speaker, r.source) for r in rows] == [
            ("Рассказчик", "llm"), ("Пупип", "operator"), ("Рассказчик", "llm"),
        ]


def test_a_source_in_the_request_body_is_ignored():
    """Клиент не может выдать свою правку за модельную, положив `source` в отрезок."""
    SessionLocal = _session()
    with SessionLocal() as db:
        text = _seed(db)
        reassign_segment(db, segment_id="c1:00000", actor_uid="u1", spans=[
            {"start": 0, "end": len(text), "speaker": "Пупип", "source": "llm"},
        ])
        db.commit()
        rows = effective_attributions(db, ["c1:00000"])["c1:00000"]
        assert {r.source for r in rows} == {"operator"}
