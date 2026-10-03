"""Stress marks in the database: versions grow, nothing is deleted, the newest wins per word."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.v2.models import V2StressMark
from app.v2.store import load_effective_stress, store_stress_marks
from app.v2.stress import StressMark


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def test_each_store_call_is_the_next_version_and_keeps_the_old_rows():
    SessionLocal = _session()
    with SessionLocal() as db:
        assert store_stress_marks(db, segment_id="ch1:00001", marks=[StressMark(0, 5, 2, "dict")]) == 1
        db.commit()
        store_stress_marks(db, segment_id="ch1:00001", marks=[StressMark(0, 5, 2, "dict"), StressMark(6, 11, 2, "context")])
        db.commit()

        rows = db.query(V2StressMark).order_by(V2StressMark.version, V2StressMark.word_start).all()
        assert [(r.version, r.word_start, r.source) for r in rows] == [(1, 0, "dict"), (2, 0, "dict"), (2, 6, "context")]


def test_versions_are_counted_per_segment():
    SessionLocal = _session()
    with SessionLocal() as db:
        store_stress_marks(db, segment_id="ch1:00001", marks=[StressMark(0, 5, 2, "dict")])
        store_stress_marks(db, segment_id="ch1:00002", marks=[StressMark(0, 5, 2, "dict")])
        db.commit()
        assert {r.version for r in db.query(V2StressMark).all()} == {1}


def test_empty_marks_write_nothing_and_do_not_bump_the_version():
    SessionLocal = _session()
    with SessionLocal() as db:
        assert store_stress_marks(db, segment_id="ch1:00001", marks=[]) == 0
        store_stress_marks(db, segment_id="ch1:00001", marks=[StressMark(0, 5, 2, "dict")])
        db.commit()
        assert db.query(V2StressMark).one().version == 1


def test_dict_marks_take_the_default_source():
    SessionLocal = _session()
    with SessionLocal() as db:
        store_stress_marks(db, segment_id="s", marks=[{"word_start": 0, "word_end": 5, "vowel_offset": 2}], source_default="operator")
        db.commit()
        assert db.query(V2StressMark).one().source == "operator"


def test_effective_marks_take_the_newest_row_per_word():
    SessionLocal = _session()
    with SessionLocal() as db:
        store_stress_marks(db, segment_id="s", marks=[StressMark(0, 5, 2, "dict"), StressMark(6, 11, 2, "context")])
        db.commit()
        # An operator corrects one word: one new row, one higher version.
        store_stress_marks(db, segment_id="s", marks=[StressMark(6, 11, 4, "operator")])
        db.commit()

        effective = load_effective_stress(db, "s")
        assert [(m.word_start, m.vowel_offset, m.source, m.version) for m in effective] == [
            (0, 2, "dict", 1), (6, 4, "operator", 2),
        ]


def test_effective_marks_are_per_segment_and_empty_when_nothing_stored():
    SessionLocal = _session()
    with SessionLocal() as db:
        store_stress_marks(db, segment_id="other", marks=[StressMark(0, 5, 2, "dict")])
        db.commit()
        assert load_effective_stress(db, "s") == []
