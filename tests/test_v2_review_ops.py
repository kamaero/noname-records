"""«Проверено» on a chapter: what it sets, what it must not undo, and what it counts."""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment
from app.v2.review_ops import approval_counts, approve_all, set_chapter_approved

BOOK = "book-1"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
        for index in (1, 2, 3):
            chapter = f"ch-{index}"
            session.add(ScriptChapter(id=chapter, book_id=BOOK, chapter_index=index, chapter_title=f"Глава {index}", status="queued"))
            session.add(V2Segment(id=f"{chapter}:00000", book_id=BOOK, chapter_id=chapter, ordinal=0,
                                  kind="paragraph", text="Текст.", char_start=0, char_end=6))
        # only the first two chapters carry attributions
        for chapter in ("ch-1", "ch-2"):
            session.add(V2Attribution(id=str(uuid.uuid4()), segment_id=f"{chapter}:00000", span_start=0,
                                      span_end=6, speaker="Рассказчик", confidence=1.0, source="llm", version=1))
        session.commit()
        yield session


def test_approving_and_taking_it_back(db):
    result = set_chapter_approved(db, chapter_id="ch-1", approved=True, actor_uid="u1")
    assert (result["status"], result["approved"]) == ("approved", True)

    back = set_chapter_approved(db, chapter_id="ch-1", approved=False, actor_uid="u1")
    assert (back["status"], back["approved"]) == ("queued", False)


def test_a_published_chapter_stays_published_when_re_approved(db):
    db.get(ScriptChapter, "ch-1").status = "published"
    db.commit()

    result = set_chapter_approved(db, chapter_id="ch-1", approved=True, actor_uid="u1")

    assert result["status"] == "published"
    assert result["approved"] is True


def test_an_unknown_chapter_is_refused(db):
    with pytest.raises(ValueError):
        set_chapter_approved(db, chapter_id="nope", approved=True, actor_uid="u1")


def test_counts_are_what_the_hub_shows(db):
    set_chapter_approved(db, chapter_id="ch-1", approved=True, actor_uid="u1")
    db.get(ScriptChapter, "ch-2").status = "published"
    db.commit()

    assert approval_counts(db, BOOK) == {"total": 3, "approved": 2, "published": 1, "attributed": 2}


def test_approve_all_takes_the_marked_up_chapters_only(db):
    result = approve_all(db, book=db.get(ScriptBook, BOOK), actor_uid="u1")

    assert result["approved_now"] == 2
    assert result["approved"] == 2
    # the chapter with no attributions is left alone: there is nothing there to read
    assert db.get(ScriptChapter, "ch-3").status == "queued"


def test_every_change_lands_in_the_operator_journal(db):
    from app.models import OperatorIntervention

    set_chapter_approved(db, chapter_id="ch-1", approved=True, actor_uid="u1", actor_name="Оператор")
    db.commit()

    row = db.query(OperatorIntervention).filter(OperatorIntervention.action_type == "v2_chapter_review").one()
    assert row.book_id == BOOK


def test_with_auto_publish_the_approved_chapter_opens_for_recording(db):
    book = db.get(ScriptBook, BOOK)
    book.auto_publish = "true"
    db.commit()
    told = []

    result = set_chapter_approved(
        db, chapter_id="ch-1", approved=True, actor_uid="u1", notify=lambda db_, b: told.append(b.id),
    )

    assert (result["status"], result["published_now"]) == ("published", True)
    assert db.get(ScriptBook, BOOK).status == "published_to_dictor"
    assert told == [BOOK]


def test_the_dictors_are_told_once_not_on_every_chapter(db):
    book = db.get(ScriptBook, BOOK)
    book.auto_publish = "true"
    db.commit()
    told = []
    notify = lambda db_, b: told.append(b.id)  # noqa: E731

    set_chapter_approved(db, chapter_id="ch-1", approved=True, actor_uid="u1", notify=notify)
    set_chapter_approved(db, chapter_id="ch-2", approved=True, actor_uid="u1", notify=notify)

    assert told == [BOOK]  # the book was already `published_to_dictor` the second time
    assert db.get(ScriptChapter, "ch-2").status == "published"


def test_taking_the_mark_off_a_published_chapter_does_not_withdraw_it(db):
    db.get(ScriptChapter, "ch-1").status = "published"
    db.commit()

    result = set_chapter_approved(db, chapter_id="ch-1", approved=False, actor_uid="u1")

    assert result["status"] == "published"


def test_without_the_switch_nothing_is_published(db):
    result = set_chapter_approved(db, chapter_id="ch-1", approved=True, actor_uid="u1", notify=lambda *a: None)

    assert (result["status"], result["published_now"]) == ("approved", False)
    assert db.get(ScriptBook, BOOK).status != "published_to_dictor"
