"""Publish a whole book to the actors, or take it back.

v1 published chapter by chapter from the validation editor; the v2 hub publishes
the book in one move. The recording screens and the dictor notifications key on
the same statuses v1 used (`script_chapters.status == "published"`,
`script_books.status == "published_to_dictor"`), so nothing downstream changes.
Only chapters that carry v2 attributions are published; chapters without any
markup stay as they are and are counted in the answer.

Once the author starts marking chapters «проверено», that mark becomes the gate:
publishing then sends the approved chapters and leaves the rest. A book nobody has
approved yet publishes whole, which is what it did before the mark existed.
"""
from __future__ import annotations

from sqlalchemy import func

from app.models import ScriptBook, ScriptChapter
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment
from app.v2.review_ops import PUBLISHED, is_approved
from app.v2.store import record_operator_intervention

PUBLISHED_TO_DICTOR = "published_to_dictor"


def _attributed_chapter_ids(db, book_id: str) -> set[str]:
    rows = (
        db.query(V2Segment.chapter_id)
        .join(V2Attribution, V2Attribution.segment_id == V2Segment.id)
        .filter(V2Segment.book_id == book_id)
        .group_by(V2Segment.chapter_id)
        .having(func.count(V2Attribution.id) > 0)
        .all()
    )
    return {row[0] for row in rows}


def publish_chapter(db, *, book: ScriptBook, chapter: ScriptChapter, notify=None) -> bool:
    """One chapter opens for recording. True when this call is what opened it.

    The book has to follow: the recording screens ask for chapters of a book that is
    `published_to_dictor`, so a single published chapter in a book that is not would be
    invisible. Notification is the caller's business and fires at most once per book
    anyway — the telegram service keeps that marker.
    """
    if str(chapter.status or "") == PUBLISHED:
        return False
    chapter.status = PUBLISHED
    chapter.updated_at = utcnow_naive()
    previous = str(book.status or "")
    book.status = PUBLISHED_TO_DICTOR
    book.updated_at = chapter.updated_at
    if notify is not None and previous != PUBLISHED_TO_DICTOR:
        try:
            notify(db, book)
        except Exception:  # noqa: BLE001 — a failed message must not undo a publish
            pass
    return True


def publish_book(db, *, book: ScriptBook, actor_uid: str, actor_name: str = "", notify=None) -> dict:
    """Attributed chapters → `published`, the book → `published_to_dictor`."""
    ready = _attributed_chapter_ids(db, book.id)
    chapters = db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id).all()
    approved = {chapter.id for chapter in chapters if is_approved(chapter)}
    # The author's mark decides once it exists; before that, everything marked up goes.
    if approved:
        ready &= approved
    published = skipped = 0
    now = utcnow_naive()
    for chapter in chapters:
        if chapter.id in ready:
            if chapter.status != "published":
                chapter.status = "published"
                chapter.updated_at = now
            published += 1
        else:
            skipped += 1
    previous = str(book.status or "")
    book.status = PUBLISHED_TO_DICTOR
    book.updated_at = now
    record_operator_intervention(
        db, book=book, action_type="v2_publish", actor_uid=actor_uid, actor_name=actor_name,
        payload={"published": published, "skipped": skipped, "previous_status": previous,
                 "gated_by_approval": bool(approved)},
    )
    notified = 0
    if notify is not None and previous != PUBLISHED_TO_DICTOR:
        try:
            sent = notify(db, book)
            notified = int(sent[0] if isinstance(sent, tuple) else (sent or 0))
        except Exception:  # noqa: BLE001 — a failed message must not undo a publish
            notified = 0
    return {"published": published, "skipped": skipped, "previous_status": previous,
            "notified": notified, "gated_by_approval": bool(approved)}


def unpublish_book(db, *, book: ScriptBook, actor_uid: str, actor_name: str = "") -> dict:
    """Back to review: published chapters → `approved`, the book → `author_review`."""
    chapters = db.query(ScriptChapter).filter(
        ScriptChapter.book_id == book.id, ScriptChapter.status == "published"
    ).all()
    now = utcnow_naive()
    for chapter in chapters:
        chapter.status = "approved"
        chapter.updated_at = now
    previous = str(book.status or "")
    book.status = "author_review"
    book.updated_at = now
    record_operator_intervention(
        db, book=book, action_type="v2_unpublish", actor_uid=actor_uid, actor_name=actor_name,
        payload={"unpublished": len(chapters), "previous_status": previous},
    )
    return {"unpublished": len(chapters), "previous_status": previous}
