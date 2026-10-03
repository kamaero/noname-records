"""Money in the cast table comes from the v2 markup, not from v1 artifacts.

v1 counted a role's lines by parsing the generated fountain text. v2 never writes
that text, so the old path counted zero for every role and the cast table showed
zero rubles for a fully attributed book. These tests pin the v2 count: one line per
effective attribution span, words from the span's own text, seconds from the words,
and the rate a role inherits when it has none of its own.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import BookBudget, Character, CharacterBudgetSnapshot, ScriptBook, ScriptChapter, StudioSettings
from app.v2.budget_ops import rebuild_v2_budget, v2_budget_is_stale
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-1"
CH = "ch-1"
NARRATION = "Дгарнин сидел в изгибе ветвей и молчал."          # 7 words
REPLY = "- Нас стало слишком много, - сказал Дгарнин."          # 7 words


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
        session.add(ScriptChapter(id=CH, book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
        session.add(Character(id="c-narrator", book_id=BOOK, name="Рассказчик"))
        session.add(Character(id="c-dgarnin", book_id=BOOK, name="Дгарнин", aliases="Дгарни́н"))
        session.add(Character(id="c-silent", book_id=BOOK, name="Пупип"))
        session.add(BookBudget(id="b1", book_id=BOOK))
        for ordinal, text in enumerate((NARRATION, REPLY)):
            session.add(V2Segment(
                id=f"{CH}:{ordinal:05d}", book_id=BOOK, chapter_id=CH, ordinal=ordinal,
                kind="paragraph", text=text, char_start=0, char_end=len(text),
            ))
        session.commit()
        yield session


def _attr(segment_id, speaker, *, version=1, start=0, end=None, text=""):
    return V2Attribution(
        id=str(uuid.uuid4()), segment_id=segment_id, span_start=start,
        span_end=len(text) if end is None else end, speaker=speaker,
        confidence=0.9, source="llm", version=version,
    )


def _mark_up(db):
    db.add(_attr(f"{CH}:00000", "Рассказчик", text=NARRATION))
    db.add(_attr(f"{CH}:00001", "Дгарни́н", text=REPLY))
    db.commit()


def _snapshots(db):
    return {
        row.character_id: row
        for row in db.query(CharacterBudgetSnapshot).filter(CharacterBudgetSnapshot.book_id == BOOK).all()
    }


def test_lines_and_seconds_come_from_the_attributions(db):
    _mark_up(db)

    report = rebuild_v2_budget(db, BOOK)
    rows = _snapshots(db)

    assert report["characters"] == 3
    # 7 words at 120 words per minute = 3.5 s, rounded to 4
    assert (rows["c-narrator"].lines_count, rows["c-narrator"].approx_seconds) == (1, 4)
    # the alias «Дгарни́н» is the same role as «Дгарнин»
    assert (rows["c-dgarnin"].lines_count, rows["c-dgarnin"].approx_seconds) == (1, 4)


def test_a_role_that_never_speaks_is_zeroed_rather_than_left_stale(db):
    db.add(CharacterBudgetSnapshot(
        id="s-old", book_id=BOOK, character_id="c-silent", lines_count=99,
        approx_seconds=600, fact_seconds=0, calc_mode="rate_plan", total_rub=10000,
    ))
    _mark_up(db)

    rebuild_v2_budget(db, BOOK)
    silent = _snapshots(db)["c-silent"]

    assert (silent.lines_count, silent.approx_seconds, silent.total_rub) == (0, 0, 0)


def test_only_the_newest_version_of_a_span_counts(db):
    _mark_up(db)
    # the operator moved the reply from Дгарнин to Пупип
    db.add(_attr(f"{CH}:00001", "Пупип", version=2, text=REPLY))
    db.commit()

    rebuild_v2_budget(db, BOOK)
    rows = _snapshots(db)

    assert rows["c-dgarnin"].lines_count == 0
    assert rows["c-silent"].lines_count == 1


def test_the_studio_rate_prices_a_role_that_has_no_rate_of_its_own(db):
    _mark_up(db)
    db.add(StudioSettings(id="default", default_rate_rub_per_min=1800))
    db.commit()

    rebuild_v2_budget(db, BOOK)

    # 4 s at 1800 ₽/min = 120 ₽
    assert _snapshots(db)["c-dgarnin"].total_rub == 120
    assert _snapshots(db)["c-dgarnin"].calc_mode == "rate_plan"


def test_a_role_keeps_its_own_rate_and_its_own_fixed_price(db):
    _mark_up(db)
    db.get(Character, "c-dgarnin").manual_rate_rub_per_min = 3000
    db.get(Character, "c-narrator").manual_fixed_rub = 50000
    db.commit()

    rebuild_v2_budget(db, BOOK)
    rows = _snapshots(db)

    assert rows["c-dgarnin"].total_rub == 200  # 4 s at 3000 ₽/min
    assert (rows["c-narrator"].total_rub, rows["c-narrator"].calc_mode) == (50000, "fixed")


def test_an_unknown_speaker_is_not_priced(db):
    db.add(_attr(f"{CH}:00000", "UNSURE", text=NARRATION))
    db.commit()

    report = rebuild_v2_budget(db, BOOK)

    assert report["lines"] == 0
    assert all(row.lines_count == 0 for row in _snapshots(db).values())


def test_staleness_is_what_the_cast_screen_asks_before_recomputing(db):
    assert v2_budget_is_stale(db, BOOK) is False  # nothing attributed yet
    _mark_up(db)
    assert v2_budget_is_stale(db, BOOK) is True

    rebuild_v2_budget(db, BOOK)
    db.commit()
    assert v2_budget_is_stale(db, BOOK) is False

    db.add(_attr(f"{CH}:00001", "Пупип", version=2, text=REPLY))
    db.commit()
    assert v2_budget_is_stale(db, BOOK) is True


def test_a_snapshot_left_by_a_deleted_role_is_removed_not_kept(db):
    db.add(CharacterBudgetSnapshot(
        id="s-ghost", book_id=BOOK, character_id="c-gone", lines_count=5,
        approx_seconds=60, fact_seconds=0, calc_mode="rate_plan", total_rub=1000,
    ))
    _mark_up(db)

    rebuild_v2_budget(db, BOOK)
    db.commit()

    assert "c-gone" not in _snapshots(db)
    # and the book does not stay «stale» for ever because of it
    assert v2_budget_is_stale(db, BOOK) is False
