"""Publishing a book flips the statuses the recording screens already key on."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import OperatorIntervention, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment
from app.v2.publish_ops import publish_book, unpublish_book


def _session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed(db):
    book = ScriptBook(id="b1", title="B", status="author_review", source_filename="b.txt", source_format="txt")
    db.add(book)
    db.add(ScriptChapter(id="c1", book_id="b1", chapter_index=1, chapter_title="1", status="pending_review"))
    db.add(ScriptChapter(id="c2", book_id="b1", chapter_index=2, chapter_title="2", status="pending_review"))
    db.add(V2Segment(id="c1:00000", book_id="b1", chapter_id="c1", ordinal=0, text="Да.", char_end=3))
    db.add(V2Attribution(id="a1", segment_id="c1:00000", span_start=0, span_end=3, speaker="Рассказчик", confidence=1, source="llm", version=1))
    db.add(V2Segment(id="c2:00000", book_id="b1", chapter_id="c2", ordinal=0, text="Нет.", char_end=4))
    db.commit()
    return book


def test_publish_marks_attributed_chapters_and_notifies_once():
    SessionLocal = _session()
    calls = []
    with SessionLocal() as db:
        book = _seed(db)
        result = publish_book(db, book=book, actor_uid="u1", notify=lambda db_, b: calls.append(b.id) or (3, 5))
        db.commit()
        assert result == {"published": 1, "skipped": 1, "previous_status": "author_review",
                          "notified": 3, "gated_by_approval": False}
        assert {c.id: c.status for c in db.query(ScriptChapter).all()} == {"c1": "published", "c2": "pending_review"}
        assert book.status == "published_to_dictor"
        assert calls == ["b1"]
        # A second publish does not message the actors again.
        publish_book(db, book=book, actor_uid="u1", notify=lambda db_, b: calls.append(b.id))
        assert calls == ["b1"]
        assert db.query(OperatorIntervention).filter_by(action_type="v2_publish").count() == 2


def test_unpublish_returns_the_book_to_review():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db)
        publish_book(db, book=book, actor_uid="u1")
        result = unpublish_book(db, book=book, actor_uid="u1")
        db.commit()
        assert result == {"unpublished": 1, "previous_status": "published_to_dictor"}
        assert db.get(ScriptChapter, "c1").status == "approved"
        assert book.status == "author_review"


def test_the_authors_mark_decides_which_chapters_go_out():
    """Once anything is approved, publishing sends the approved chapters and no others."""
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db)
        # both chapters are marked up now, but only the second one was read by the author
        db.add(V2Attribution(id="a2", segment_id="c2:00000", span_start=0, span_end=4,
                             speaker="Рассказчик", confidence=1, source="llm", version=1))
        db.get(ScriptChapter, "c2").status = "approved"
        db.commit()

        result = publish_book(db, book=book, actor_uid="u1")
        db.commit()

        assert result["gated_by_approval"] is True
        assert (result["published"], result["skipped"]) == (1, 1)
        assert {c.id: c.status for c in db.query(ScriptChapter).all()} == {"c1": "pending_review", "c2": "published"}
