"""Where the model did not commit to a speaker — the queue for the review pass.

Two things count as a dispute and they are not the same thing:

* `UNSURE` — the model refused to name a speaker. Deliberate: the prompt tells it to
  abstain rather than guess, and on the held-out set that abstention is what keeps
  the answered lines at 0.948 accuracy. Someone has to answer these.
* a low confidence — the model did name a speaker but hedged. The run's review pass
  already re-asked the worst of them; what survives is worth a human glance.

Only a model can hedge, so only a model's spans are read for confidence. A span an
operator settled is never a dispute, and neither is one imported from the owner's own
Google-Docs colouring — its confidence column is a placeholder, not an opinion. The
exception is anyone who deliberately wrote UNSURE: that is a question left for later
on purpose, and it stays in the queue whoever left it.
"""
from __future__ import annotations

import collections

from sqlalchemy import and_, or_, select

from app.models import ScriptChapter
from app.v2.models import V2Attribution, V2Segment
from app.v2.reader import UNSURE, _latest_versions_for_book

LOW_CONFIDENCE = 0.7
# The sources whose confidence means anything; see the note above.
MODEL_SOURCES = ("llm", "llm_review")
EXCERPT_CHARS = 160
DEFAULT_LIMIT = 200


def _excerpt(text: str, start: int, end: int) -> str:
    """The disputed span itself, trimmed to something a list can show."""
    span = str(text or "")[max(0, int(start or 0)) : max(0, int(end or 0))].strip()
    if len(span) <= EXCERPT_CHARS:
        return span
    return span[: EXCERPT_CHARS - 1].rstrip() + "…"


def disputed_spans(db, book_id: str, *, limit: int = DEFAULT_LIMIT, threshold: float = LOW_CONFIDENCE) -> dict:
    """Counts for the whole book, plus the first `limit` disputed spans in reading order."""
    latest = _latest_versions_for_book(V2Attribution, book_id)
    unsure = or_(V2Attribution.speaker == UNSURE, V2Attribution.speaker.startswith(f"{UNSURE}:"))
    hedged = and_(V2Attribution.confidence < threshold, V2Attribution.source.in_(MODEL_SOURCES))
    stmt = (
        select(
            V2Attribution.segment_id, V2Attribution.span_start, V2Attribution.span_end,
            V2Attribution.speaker, V2Attribution.confidence, V2Attribution.source,
            V2Segment.chapter_id, V2Segment.ordinal, V2Segment.text,
            ScriptChapter.chapter_index, ScriptChapter.chapter_title,
        )
        .join(latest, and_(latest.c.segment_id == V2Attribution.segment_id, latest.c.v == V2Attribution.version))
        .join(V2Segment, V2Segment.id == V2Attribution.segment_id)
        .join(ScriptChapter, ScriptChapter.id == V2Segment.chapter_id)
        .where(or_(unsure, hedged))
        .order_by(ScriptChapter.chapter_index.asc(), V2Segment.ordinal.asc(), V2Attribution.span_start.asc())
    )

    items: list[dict] = []
    by_chapter: collections.Counter = collections.Counter()
    unsure_count = 0
    for row in db.execute(stmt).all():
        (segment_id, start, end, speaker, confidence, source,
         chapter_id, ordinal, text, chapter_index, chapter_title) = row
        is_unsure = str(speaker or "").upper().startswith(UNSURE)
        unsure_count += 1 if is_unsure else 0
        by_chapter[str(int(chapter_index or 0))] += 1
        if len(items) < max(1, int(limit or DEFAULT_LIMIT)):
            items.append({
                "segment_id": str(segment_id),
                "chapter_id": str(chapter_id),
                "chapter_index": int(chapter_index or 0),
                "chapter_title": str(chapter_title or "").strip(),
                "ordinal": int(ordinal or 0),
                "span": {"start": int(start or 0), "end": int(end or 0)},
                "speaker": "" if is_unsure else str(speaker or ""),
                "confidence": round(float(confidence or 0.0), 2),
                "source": str(source or ""),
                "kind": "unsure" if is_unsure else "low",
                "excerpt": _excerpt(text, start, end),
            })

    total = sum(by_chapter.values())
    return {
        "counts": {
            "total": total,
            "unsure": unsure_count,
            "low": total - unsure_count,
            "chapters": len(by_chapter),
        },
        "by_chapter": dict(by_chapter),
        "items": items,
        "truncated": total > len(items),
        "threshold": float(threshold),
    }
