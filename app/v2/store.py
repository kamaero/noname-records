"""Put a chapter's segments into the database, and take them back out.

Re-segmenting has to be safe to repeat. The text does not change, so the ids do not
change either, and a second run must leave the table exactly as the first did — which
means replacing the chapter's rows wholesale rather than inserting alongside them. A
chapter that lost a paragraph must not keep the tail of the previous run: a ghost
segment nobody can see is a segment an annotation can still point at.

Attributions are the opposite case. They are never replaced: a new answer for a
segment is a new row with the next version, and the old one stays as the record of
what the model said before. All spans of one segment written together share one
version, because they are one answer about one paragraph, not several.

Stress marks follow the attribution rule for writing and differ in reading: a
segment's marks written together share the next version, but the effective set is
the newest row *per word*, so an operator can correct one word with one row and a
full re-run can still replace everything.
"""
from __future__ import annotations

import collections
import uuid

from sqlalchemy import func

from app.v2.models import V2Attribution, V2Segment, V2StressMark


def store_chapter_segments(db, *, book_id: str, chapter_id: str, segments) -> int:
    """Replace this chapter's segments with `segments`. Returns how many were stored."""
    db.query(V2Segment).filter(V2Segment.chapter_id == chapter_id).delete(synchronize_session=False)
    for segment in segments:
        db.add(V2Segment(
            id=segment.id,
            book_id=book_id,
            chapter_id=chapter_id,
            ordinal=segment.ordinal,
            kind=segment.kind,
            text=segment.text,
            char_start=segment.char_start,
            char_end=segment.char_end,
        ))
    return len(segments)


def store_attributions(db, records) -> int:
    """Add `records` as the next version of each segment they cover. Returns how many rows."""
    by_segment: dict[str, list[dict]] = collections.OrderedDict()
    for record in records:
        by_segment.setdefault(str(record["unit_id"]), []).append(record)
    if not by_segment:
        return 0

    current = dict(
        db.query(V2Attribution.segment_id, func.max(V2Attribution.version))
        .filter(V2Attribution.segment_id.in_(list(by_segment)))
        .group_by(V2Attribution.segment_id)
        .all()
    )

    stored = 0
    for segment_id, spans in by_segment.items():
        version = int(current.get(segment_id) or 0) + 1
        for record in spans:
            db.add(V2Attribution(
                id=str(uuid.uuid4()),
                segment_id=segment_id,
                span_start=int(record["span_start"]),
                span_end=int(record["span_end"]),
                speaker=str(record["speaker"]),
                confidence=float(record.get("confidence") or 0.0),
                source=str(record.get("source") or "llm"),
                version=version,
            ))
            stored += 1
    return stored


def load_chapter_segments(db, *, chapter_id: str) -> list[V2Segment]:
    return (
        db.query(V2Segment)
        .filter(V2Segment.chapter_id == chapter_id)
        .order_by(V2Segment.ordinal.asc())
        .all()
    )


def store_stress_marks(db, *, segment_id: str, marks, source_default: str = "dict") -> int:
    """Add `marks` for one segment as its next version. Returns how many rows.

    Same convention as attributions: a version is one answer about one segment, so
    every mark written together shares the number, and nothing is deleted. An empty
    list writes nothing and does not bump the version — silence is not an answer.
    Each mark is a `StressMark` or a dict with word_start, word_end, vowel_offset and
    optionally source; `source_default` fills a missing source.
    """
    rows = list(marks or [])
    if not rows:
        return 0
    current = (
        db.query(func.max(V2StressMark.version))
        .filter(V2StressMark.segment_id == segment_id)
        .scalar()
    )
    version = int(current or 0) + 1
    for mark in rows:
        db.add(V2StressMark(**_stress_row(mark, segment_id, version, source_default)))
    return len(rows)


def _stress_row(mark, segment_id: str, version: int, source_default: str) -> dict:
    get = mark.get if isinstance(mark, dict) else lambda name, _m=mark: getattr(_m, name, None)
    return {
        "id": str(uuid.uuid4()),
        "segment_id": segment_id,
        "word_start": int(get("word_start")),
        "word_end": int(get("word_end")),
        "vowel_offset": int(get("vowel_offset")),
        "source": str(get("source") or source_default),
        "version": version,
    }


def store_stress_marks_bulk(db, marks_by_segment: dict, *, source_default: str = "dict") -> int:
    """`store_stress_marks` for many segments at once. Returns how many rows.

    Same rule per segment — its next version, nothing deleted, an empty list writes
    nothing — but one version query per 500 segments and one multi-row insert,
    because an operator's rule for a common name touches thousands of segments and
    the one-segment path costs a round trip and an ORM object per row.
    """
    from sqlalchemy import insert

    ids = [str(segment_id) for segment_id, marks in marks_by_segment.items() if list(marks or [])]
    if not ids:
        return 0
    rows: list[dict] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        current = dict(
            db.query(V2StressMark.segment_id, func.max(V2StressMark.version))
            .filter(V2StressMark.segment_id.in_(chunk))
            .group_by(V2StressMark.segment_id)
            .all()
        )
        for segment_id in chunk:
            version = int(current.get(segment_id) or 0) + 1
            rows.extend(_stress_row(mark, segment_id, version, source_default) for mark in marks_by_segment[segment_id])
    db.execute(insert(V2StressMark), rows)
    return len(rows)


def load_effective_stress(db, segment_id: str) -> list[V2StressMark]:
    """The marks that currently count for a segment, in text order.

    Per word, the row with the highest version wins. That lets one operator row
    override one word without re-writing the whole segment, and lets a full re-run
    override everything — the two ways a correction arrives.
    """
    rows = (
        db.query(V2StressMark)
        .filter(V2StressMark.segment_id == segment_id)
        .order_by(V2StressMark.version.asc(), V2StressMark.word_start.asc())
        .all()
    )
    by_word: dict[tuple[int, int], V2StressMark] = {}
    for row in rows:
        by_word[(row.word_start, row.word_end)] = row
    return sorted(by_word.values(), key=lambda r: r.word_start)


def record_operator_intervention(db, *, book, action_type: str, actor_uid: str, actor_name: str = "",
                                 reason: str = "", payload: dict | None = None):
    """One audit row for an operator's v2 action, in the shape v1 writes for `pronunciation_update`.

    v1 keeps its audit trail in `operator_interventions` and reads it back (the
    pronunciation rollback finds its source there); v2 writes into the same table so
    one place answers «who changed what» for both pipelines.

    `book=None` is accepted on purpose: an audio file's `book_code` is a derived
    string, not a foreign key, and can outlive or predate the book it once matched.
    Losing the book must never mean losing the row — the trace stays, only with an
    empty `book_id`, rather than being silently skipped.
    """
    import json

    from app.models import OperatorIntervention
    from app.time_utils import utcnow_naive

    row = OperatorIntervention(
        book_id=str(book.id) if book is not None else "",
        chapter_id="",
        pipeline_run_id=str(getattr(book, "current_pipeline_run_id", "") or ""),
        actor_user_id=str(actor_uid or ""),
        actor_name=str(actor_name or ""),
        action_type=action_type,
        reason=str(reason or "")[:500],
        payload_json=json.dumps(payload or {}, ensure_ascii=False),
        created_at=utcnow_naive(),
    )
    db.add(row)
    return row


def ensure_v2_tables(engine) -> list[str]:
    """Create the v2 tables a database is missing. Returns the names created.

    For scratch copies that never saw revisions 0008/0009: an experiment stays one
    command. On production the tables come from the migrations and nowhere else —
    the scripts say out loud when they create something.
    """
    import sqlalchemy as sa

    from app.v2.models import V2Run

    existing = set(sa.inspect(engine).get_table_names())
    tables = [V2Segment.__table__, V2Attribution.__table__, V2StressMark.__table__, V2Run.__table__]
    missing = [table for table in tables if table.name not in existing]
    if missing:
        V2Segment.metadata.create_all(bind=engine, tables=missing)
    return [table.name for table in missing]
