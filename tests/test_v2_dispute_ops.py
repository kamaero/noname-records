"""The disputed lines of a book: where the model did not commit to a speaker.

The model abstains rather than guesses — it says UNSURE, or answers with a low
confidence — and that is the whole point of the review pass. But until now those
places existed only as an amber frame somewhere in 60 chapters: no count, no list,
no way to work through them. This is the queue behind that list.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ScriptBook, ScriptChapter
from app.v2.dispute_ops import disputed_spans
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-1"
CH1, CH2 = "ch-1", "ch-2"
TEXT = "- Кто здесь? - спросил голос из темноты, и никто ему не ответил."


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
        session.add(ScriptChapter(id=CH1, book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
        session.add(ScriptChapter(id=CH2, book_id=BOOK, chapter_index=2, chapter_title="Глава 2"))
        for chapter in (CH1, CH2):
            for ordinal in range(3):
                session.add(V2Segment(
                    id=f"{chapter}:{ordinal:05d}", book_id=BOOK, chapter_id=chapter, ordinal=ordinal,
                    kind="paragraph", text=TEXT, char_start=0, char_end=len(TEXT),
                ))
        session.commit()
        yield session


def _attr(segment_id, speaker, confidence, *, version=1, source="llm", start=0, end=len(TEXT)):
    return V2Attribution(
        id=str(uuid.uuid4()), segment_id=segment_id, span_start=start, span_end=end,
        speaker=speaker, confidence=confidence, source=source, version=version,
    )


def test_unsure_and_low_confidence_are_the_two_kinds_of_dispute(db):
    db.add(_attr(f"{CH1}:00000", "UNSURE", 0.0))
    db.add(_attr(f"{CH1}:00001", "Дгарнин", 0.42))
    db.add(_attr(f"{CH1}:00002", "Дгарнин", 0.98))
    db.commit()

    result = disputed_spans(db, BOOK)

    assert result["counts"] == {"total": 2, "unsure": 1, "low": 1, "chapters": 1}
    assert [item["kind"] for item in result["items"]] == ["unsure", "low"]
    assert result["items"][0]["chapter_index"] == 1
    assert result["items"][0]["excerpt"].startswith("- Кто здесь?")


def test_only_a_model_can_hedge(db):
    # the operator settled this one at a low confidence on purpose
    db.add(_attr(f"{CH1}:00000", "Дгарнин", 0.3, source="operator"))
    # imported markup carries the owner's own colours; its confidence means nothing
    db.add(_attr(f"{CH1}:00002", "Дгарнин", 0.0, source="import"))
    # …but anyone who deliberately says «не знаю» stays in the queue
    db.add(_attr(f"{CH1}:00001", "UNSURE", 1.0, source="operator"))
    db.commit()

    result = disputed_spans(db, BOOK)

    assert result["counts"]["total"] == 1
    assert result["items"][0]["segment_id"] == f"{CH1}:00001"


def test_only_the_newest_version_of_a_span_is_judged(db):
    db.add(_attr(f"{CH1}:00000", "UNSURE", 0.0))
    db.add(_attr(f"{CH1}:00000", "Дгарнин", 1.0, version=2, source="operator"))
    db.commit()

    assert disputed_spans(db, BOOK)["counts"]["total"] == 0


def test_the_list_is_ordered_by_chapter_and_capped_with_a_flag(db):
    db.add(_attr(f"{CH2}:00000", "UNSURE", 0.0))
    db.add(_attr(f"{CH1}:00002", "UNSURE", 0.0))
    db.add(_attr(f"{CH1}:00001", "UNSURE", 0.0))
    db.commit()

    result = disputed_spans(db, BOOK, limit=2)

    assert [(item["chapter_index"], item["ordinal"]) for item in result["items"]] == [(1, 1), (1, 2)]
    assert result["truncated"] is True
    assert result["counts"]["total"] == 3
    assert result["counts"]["chapters"] == 2


def test_by_chapter_counts_let_the_rail_show_where_the_work_is(db):
    db.add(_attr(f"{CH1}:00000", "UNSURE", 0.0))
    db.add(_attr(f"{CH2}:00000", "UNSURE", 0.0))
    db.add(_attr(f"{CH2}:00001", "Дгарнин", 0.5))
    db.commit()

    assert disputed_spans(db, BOOK)["by_chapter"] == {"1": 1, "2": 2}
