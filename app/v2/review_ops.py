"""«Проверено» — the author's own mark on a chapter.

The pipeline leaves every chapter `queued` and publishing turns it into `published`,
which left no way to say the thing the workflow is actually built around: this chapter
has been read by the author and is fine. That mark is `approved`, the same status
unpublishing already returns a chapter to, so nothing new has to be taught to the
recording screens.

Approving is not publishing — except when the book says it is. With `auto_publish`
on, the chapter the author has just approved opens for recording immediately, which is
what an author reading a book over several days actually wants: the dictors start on
chapter one while chapter forty is still being read. Unapproving must be possible the
moment the author changes their mind, so both directions are one call and both are
recorded in the operator journal.
"""
from __future__ import annotations

from sqlalchemy import func

from app.models import ScriptBook, ScriptChapter
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment
from app.v2.store import record_operator_intervention

APPROVED = "approved"
PUBLISHED = "published"
#: what a chapter goes back to when the author takes their approval away
UNAPPROVED = "queued"
ACTION = "v2_chapter_review"


def is_approved(chapter: ScriptChapter) -> bool:
    """Published chapters count as approved: they were approved and then let out."""
    return str(getattr(chapter, "status", "") or "") in (APPROVED, PUBLISHED)


def auto_publish_on(book) -> bool:
    return str(getattr(book, "auto_publish", "") or "").strip().lower() == "true"


def set_chapter_approved(db, *, chapter_id: str, approved: bool, actor_uid: str, actor_name: str = "",
                         notify=None) -> dict:
    """Mark one chapter checked by the author, or take the mark back.

    With the book's `auto_publish` on, approving also opens the chapter for recording;
    `notify` is then the dictors' message, which the telegram service sends once per
    book. Taking the mark back never unpublishes: a chapter the dictors are already
    reading is not withdrawn behind their backs — that is what «Вернуть на проверку»
    is for.
    """
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        raise ValueError("chapter_not_found")
    previous = str(chapter.status or "")
    book = db.get(ScriptBook, chapter.book_id)
    published_now = False
    if approved:
        # A published chapter stays published: the author re-reading it changes nothing.
        chapter.status = PUBLISHED if previous == PUBLISHED else APPROVED
        if book is not None and auto_publish_on(book) and chapter.status != PUBLISHED:
            from app.v2.publish_ops import publish_chapter

            published_now = publish_chapter(db, book=book, chapter=chapter, notify=notify)
    else:
        chapter.status = UNAPPROVED if previous != PUBLISHED else PUBLISHED
    chapter.updated_at = utcnow_naive()

    if book is not None:
        record_operator_intervention(
            db, book=book, action_type=ACTION, actor_uid=actor_uid, actor_name=actor_name,
            reason="v2: проверка главы автором",
            payload={"chapter_id": chapter.id, "chapter_index": int(chapter.chapter_index or 0),
                     "approved": bool(approved), "previous_status": previous,
                     "published_now": published_now},
        )
    return {
        "chapter_id": chapter.id,
        "chapter_index": int(chapter.chapter_index or 0),
        "status": chapter.status,
        "approved": is_approved(chapter),
        "published": str(chapter.status or "") == PUBLISHED,
        "published_now": published_now,
    }


def approval_counts(db, book_id: str) -> dict:
    """`{total, approved, published, attributed}` — what the hub's review step shows."""
    rows = db.query(ScriptChapter).filter(ScriptChapter.book_id == book_id).all()
    attributed = {
        row[0]
        for row in db.query(V2Segment.chapter_id)
        .join(V2Attribution, V2Attribution.segment_id == V2Segment.id)
        .filter(V2Segment.book_id == book_id)
        .group_by(V2Segment.chapter_id)
        .having(func.count(V2Attribution.id) > 0)
        .all()
    }
    return {
        "total": len(rows),
        "approved": sum(1 for row in rows if is_approved(row)),
        "published": sum(1 for row in rows if str(row.status or "") == PUBLISHED),
        "attributed": len(attributed),
    }


def approve_all(db, *, book: ScriptBook, actor_uid: str, actor_name: str = "") -> dict:
    """Every attributed chapter at once — the answer to «я прочитал всё»."""
    counts = approval_counts(db, book.id)
    attributed_ids = {
        row[0]
        for row in db.query(V2Segment.chapter_id)
        .join(V2Attribution, V2Attribution.segment_id == V2Segment.id)
        .filter(V2Segment.book_id == book.id)
        .group_by(V2Segment.chapter_id)
        .all()
    }
    changed = 0
    now = utcnow_naive()
    for chapter in db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id).all():
        if chapter.id in attributed_ids and not is_approved(chapter):
            chapter.status = APPROVED
            chapter.updated_at = now
            changed += 1
    record_operator_intervention(
        db, book=book, action_type=ACTION, actor_uid=actor_uid, actor_name=actor_name,
        reason="v2: проверены все главы", payload={"approved_now": changed, "before": counts},
    )
    return {"approved_now": changed, **approval_counts(db, book.id)}
