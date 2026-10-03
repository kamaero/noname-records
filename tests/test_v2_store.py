"""Putting segments into the database and taking them back out.

Re-segmenting must be safe to repeat: the text does not change, so the ids do not
change, and a second run has to leave the table as the first did rather than
duplicating a chapter or renumbering it.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.v2.models import V2Segment
from app.v2.segmenter import segment_chapter
from app.v2.store import load_chapter_segments, store_chapter_segments

CHAPTER = """Глава 1. Ничего особенного

Дгарнин сидел в изгибе ветвей.

- Нас стало слишком много, - сказал Дгарнин.
"""


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def test_segments_go_in_and_come_back_in_order():
    SessionLocal = _session()
    with SessionLocal() as db:
        segments = segment_chapter(CHAPTER, chapter_id="ch1")
        store_chapter_segments(db, book_id="kp", chapter_id="ch1", segments=segments)
        db.commit()

        back = load_chapter_segments(db, chapter_id="ch1")
        assert [s.id for s in back] == [s.id for s in segments]
        assert [s.text for s in back] == [s.text for s in segments]
        assert [s.ordinal for s in back] == [0, 1, 2]
        assert back[0].kind == "heading"


def test_running_twice_leaves_one_copy():
    SessionLocal = _session()
    with SessionLocal() as db:
        segments = segment_chapter(CHAPTER, chapter_id="ch1")
        store_chapter_segments(db, book_id="kp", chapter_id="ch1", segments=segments)
        db.commit()
        store_chapter_segments(db, book_id="kp", chapter_id="ch1", segments=segments)
        db.commit()

        assert db.query(V2Segment).count() == len(segments)


def test_a_chapter_that_lost_a_paragraph_does_not_keep_a_ghost():
    """Re-segmenting shorter text must not leave the tail of the previous run behind."""
    SessionLocal = _session()
    with SessionLocal() as db:
        store_chapter_segments(
            db, book_id="kp", chapter_id="ch1",
            segments=segment_chapter(CHAPTER, chapter_id="ch1"),
        )
        db.commit()

        shorter = "Глава 1. Ничего особенного\n\nДгарнин сидел в изгибе ветвей.\n"
        store_chapter_segments(
            db, book_id="kp", chapter_id="ch1",
            segments=segment_chapter(shorter, chapter_id="ch1"),
        )
        db.commit()

        assert db.query(V2Segment).count() == 2


def test_chapters_do_not_disturb_each_other():
    SessionLocal = _session()
    with SessionLocal() as db:
        store_chapter_segments(db, book_id="kp", chapter_id="ch1",
                               segments=segment_chapter(CHAPTER, chapter_id="ch1"))
        store_chapter_segments(db, book_id="kp", chapter_id="ch2",
                               segments=segment_chapter(CHAPTER, chapter_id="ch2"))
        db.commit()

        assert len(load_chapter_segments(db, chapter_id="ch1")) == 3
        assert db.query(V2Segment).count() == 6


def test_storing_nothing_clears_the_chapter():
    SessionLocal = _session()
    with SessionLocal() as db:
        store_chapter_segments(db, book_id="kp", chapter_id="ch1",
                               segments=segment_chapter(CHAPTER, chapter_id="ch1"))
        db.commit()
        store_chapter_segments(db, book_id="kp", chapter_id="ch1", segments=[])
        db.commit()

        assert load_chapter_segments(db, chapter_id="ch1") == []


# --- attributions -------------------------------------------------------------


def _attribution_records(segments, speaker="Рассказчик", confidence=0.9, source="llm"):
    from app.v2.units import units_from_segments

    return [
        {**unit.as_record(span_start=0, span_end=len(unit.text), speaker=speaker, confidence=confidence),
         "source": source}
        for unit in units_from_segments(segments)
    ]


def test_attributions_are_stored_with_source_and_confidence():
    from app.v2.models import V2Attribution
    from app.v2.store import store_attributions

    SessionLocal = _session()
    with SessionLocal() as db:
        segments = segment_chapter(CHAPTER, chapter_id="ch1")
        store_chapter_segments(db, book_id="kp", chapter_id="ch1", segments=segments)
        stored = store_attributions(db, _attribution_records(segments, speaker="Дгарнин", confidence=0.8))
        db.commit()

        rows = db.query(V2Attribution).order_by(V2Attribution.segment_id).all()
        assert stored == 3 and len(rows) == 3
        assert {r.segment_id for r in rows} == {s.id for s in segments}
        assert all(r.speaker == "Дгарнин" and r.confidence == 0.8 and r.source == "llm" for r in rows)
        assert all(r.version == 1 for r in rows)
        assert all(r.span_end == len(s.text) for r, s in zip(rows, sorted(segments, key=lambda s: s.id)))


def test_a_second_run_gets_the_next_version_and_keeps_the_first():
    from app.v2.models import V2Attribution
    from app.v2.store import store_attributions

    SessionLocal = _session()
    with SessionLocal() as db:
        segments = segment_chapter(CHAPTER, chapter_id="ch1")
        store_attributions(db, _attribution_records(segments))
        db.commit()
        store_attributions(db, _attribution_records(segments, source="llm_review"))
        db.commit()

        assert db.query(V2Attribution).count() == 6
        versions = {(r.segment_id, r.version): r.source for r in db.query(V2Attribution).all()}
        assert versions[(segments[1].id, 1)] == "llm"
        assert versions[(segments[1].id, 2)] == "llm_review"


def test_spans_of_one_segment_share_a_version():
    from app.v2.models import V2Attribution
    from app.v2.store import store_attributions
    from app.v2.units import units_from_segments

    SessionLocal = _session()
    with SessionLocal() as db:
        segments = segment_chapter(CHAPTER, chapter_id="ch1")
        unit = units_from_segments(segments)[2]
        store_attributions(db, [
            {**unit.as_record(span_start=0, span_end=10, speaker="Дгарнин", confidence=0.9), "source": "llm"},
            {**unit.as_record(span_start=10, span_end=len(unit.text), speaker="Рассказчик", confidence=0.9), "source": "llm"},
        ])
        db.commit()
        store_attributions(db, _attribution_records([segments[2]]))
        db.commit()

        rows = db.query(V2Attribution).filter(V2Attribution.segment_id == unit.id).all()
        assert sorted(r.version for r in rows) == [1, 1, 2]


def test_storing_no_attributions_is_a_no_op():
    from app.v2.store import store_attributions

    SessionLocal = _session()
    with SessionLocal() as db:
        assert store_attributions(db, []) == 0
