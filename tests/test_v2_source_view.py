"""The original text of a chapter, cut so it lines up with the script.

The left pane of «Сверка с оригиналом» must show the author's own text, not the
segments re-joined: that is the only way an author can see that nothing was lost
on the way in. So the blocks are sliced out of `ScriptChapter.source_text` at the
segments' own offsets, and any prose between two segments comes back as a `gap`
block — the thing the script does not cover.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ScriptBook, ScriptChapter
from app.v2.models import V2Segment
from app.v2.segmenter import segment_chapter
from app.v2.source_view import build_source_view
from app.v2.store import store_chapter_segments

BOOK = "book-1"
CH = "ch-1"

SOURCE = """Глава 1

Дгарнин сидел в изгибе ветвей.

- Нас стало слишком много, - сказал Дгарнин.
"""


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
        session.add(ScriptChapter(id=CH, book_id=BOOK, chapter_index=1, chapter_title="Глава 1", source_text=SOURCE))
        session.commit()
        yield session


def _segment(db, text=SOURCE):
    store_chapter_segments(db, book_id=BOOK, chapter_id=CH, segments=segment_chapter(text, chapter_id=CH))
    db.commit()


def test_unknown_chapter_is_none(db):
    assert build_source_view(db, "nope") is None


def test_blocks_follow_the_segments_and_carry_their_ids(db):
    _segment(db)
    view = build_source_view(db, CH)

    assert view["aligned"] is True
    assert [block["kind"] for block in view["blocks"]] == ["heading", "paragraph", "paragraph"]
    assert [block["segment_id"] for block in view["blocks"]] == [f"{CH}:0000{i}" for i in range(3)]
    assert view["blocks"][1]["text"] == "Дгарнин сидел в изгибе ветвей."
    assert view["counts"] == {"blocks": 3, "segments": 3, "gaps": 0, "gap_chars": 0}


def test_text_the_script_does_not_cover_comes_back_as_a_gap(db):
    _segment(db)
    # A paragraph that never became a segment — a chapter segmented from an older
    # revision, or a block the segmenter skipped. The offsets of what is there
    # still fit, so the pane stays aligned and shows the hole instead of hiding it.
    db.delete(db.get(V2Segment, f"{CH}:00001"))
    db.commit()

    view = build_source_view(db, CH)
    gaps = [block for block in view["blocks"] if block["kind"] == "gap"]

    assert view["aligned"] is True
    assert [block["text"] for block in gaps] == ["Дгарнин сидел в изгибе ветвей."]
    assert [block["segment_id"] for block in gaps] == [""]
    assert view["counts"]["gaps"] == 1
    assert view["counts"]["gap_chars"] == len("Дгарнин сидел в изгибе ветвей.")


def test_text_appended_after_segmentation_shows_up_at_the_end(db):
    _segment(db)
    chapter = db.get(ScriptChapter, CH)
    chapter.source_text = SOURCE + "\nОн ошибался.\n"
    db.commit()

    view = build_source_view(db, CH)

    assert view["aligned"] is True
    assert view["blocks"][-1] == {"segment_id": "", "ordinal": -1, "kind": "gap", "text": "Он ошибался."}


def test_offsets_that_no_longer_fit_fall_back_to_the_segment_text(db):
    _segment(db)
    chapter = db.get(ScriptChapter, CH)
    chapter.source_text = "Совсем другой текст."
    db.commit()

    view = build_source_view(db, CH)

    assert view["aligned"] is False
    assert [block["text"] for block in view["blocks"]] == [
        "Глава 1", "Дгарнин сидел в изгибе ветвей.", "- Нас стало слишком много, - сказал Дгарнин.",
    ]
    assert view["counts"]["gaps"] == 0


def test_chapter_without_segments_still_shows_its_original(db):
    view = build_source_view(db, CH)

    assert view["counts"]["segments"] == 0
    assert [block["kind"] for block in view["blocks"]] == ["gap", "gap", "gap"]
    assert view["blocks"][0]["text"] == "Глава 1"


def test_chapter_without_source_text_falls_back_to_segments(db):
    _segment(db)
    chapter = db.get(ScriptChapter, CH)
    chapter.source_text = ""
    db.commit()

    view = build_source_view(db, CH)

    assert view["aligned"] is False
    assert len(view["blocks"]) == 3
    assert view["blocks"][2]["segment_id"] == f"{CH}:00002"
