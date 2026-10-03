"""The author profile as seen from one book: what is linked, what is not, and the two syncs.

`sync_book_to_author_profile` and `apply_author_profile_to_book` already exist and
are already the right functions; what v2 adds is a page that shows their effect on
one book — how many characters are linked, how many stresses the profile knows —
and an audit row per run, so «when was this last synced» has an answer without
reading logs. Nothing here decides conflicts; that stays in the two services.
"""
from __future__ import annotations

import json

from sqlalchemy import func, select

from app.models import Author, AuthorCharacter, AuthorPronunciation, Character, OperatorIntervention, ScriptBook
from app.services.author_profile import (
    _parse_pronunciation_notes,
    normalize_name,
    apply_author_profile_to_book,
    sync_book_to_author_profile,
)
from app.v2.models import V2Attribution, V2Segment, V2StressMark
from app.v2.reader import _book_title, _latest_versions_for_book
from app.v2.store import record_operator_intervention
from app.time_utils import iso_utc

SYNC_ACTION = "v2_profile_sync"
APPLY_ACTION = "v2_profile_apply"
BIND_ACTION = "v2_profile_bind"
# Below this many matching roles a suggestion is noise, not a finding.
SUGGEST_MIN_MATCHES = 3


def _count(db, stmt) -> int:
    return int(db.execute(stmt).scalar() or 0)


def _v2_counts(db, book_id: str) -> dict:
    segment_ids = select(V2Segment.id).where(V2Segment.book_id == book_id)
    latest_marks = _latest_versions_for_book(V2StressMark, book_id)
    return {
        "v2_segments": _count(db, select(func.count()).select_from(V2Segment).where(V2Segment.book_id == book_id)),
        "v2_attributed_segments": _count(
            db, select(func.count(func.distinct(V2Attribution.segment_id))).where(V2Attribution.segment_id.in_(segment_ids)),
        ),
        "v2_stress_marks": _count(
            db,
            select(func.count()).select_from(V2StressMark).join(
                latest_marks,
                (latest_marks.c.segment_id == V2StressMark.segment_id) & (latest_marks.c.v == V2StressMark.version),
            ),
        ),
    }


def _last_intervention(db, book_id: str, action_type: str) -> dict | None:
    row = (
        db.query(OperatorIntervention)
        .filter(OperatorIntervention.book_id == book_id, OperatorIntervention.action_type == action_type)
        .order_by(OperatorIntervention.created_at.desc())
        .first()
    )
    if row is None:
        return None
    try:
        summary = json.loads(row.payload_json or "{}")
    except ValueError:
        summary = {}
    return {"at": iso_utc(row.created_at), "summary": summary}


def _suggest_author(db, book_id: str) -> dict | None:
    """The author whose roster the book's own role names match best.

    Ingest guesses the author from the title and the file name, which says nothing
    for «Крылья полумрака». The roles do say something: a book of an author already
    in the system shares dozens of names with that author's roster.
    """
    names = {
        normalize_name(name)
        for (name,) in db.query(Character.name).filter(Character.book_id == book_id).all()
        if str(name or "").strip()
    }
    if not names:
        return None
    best: dict | None = None
    for author in db.query(Author).all():
        roster = {
            normalize_name(name)
            for (name,) in db.query(AuthorCharacter.canonical_name)
            .filter(AuthorCharacter.author_id == author.id).all()
            if str(name or "").strip()
        }
        matched = len(names & roster)
        if matched >= SUGGEST_MIN_MATCHES and (best is None or matched > best["matched"]):
            best = {"author_id": author.id, "name": str(author.name or ""), "matched": matched}
    return best


def bind_author(db, *, book: ScriptBook, author_id: str, actor_uid: str, actor_name: str = "") -> dict:
    """Point a book at an author (or at none) and pull the profile down at once.

    Binding without applying would leave the owner with a screen that says the author
    is known and a cast that is still empty, so the two happen together; applying only
    fills blanks, so re-binding never overwrites a decision.
    """
    wanted = str(author_id or "").strip()
    author = db.get(Author, wanted) if wanted else None
    if wanted and author is None:
        raise ValueError("author_not_found")

    book.author_id = wanted
    applied = None
    if author is not None:
        applied = apply_author_profile_to_book(db, book)
    record_operator_intervention(
        db, book=book, action_type=BIND_ACTION, actor_uid=actor_uid, actor_name=actor_name,
        reason="v2: привязка книги к автору",
        payload={"author_id": wanted, "author_name": str(getattr(author, "name", "") or ""), "applied": applied},
    )
    return {"author_id": wanted, "author_name": str(getattr(author, "name", "") or ""), "applied": applied}


def book_profile(db, book_id: str) -> dict | None:
    """The profile page's numbers for one book; None when the book is unknown."""
    book = db.get(ScriptBook, book_id)
    if book is None:
        return None
    author_id = str(book.author_id or "")
    author = db.get(Author, author_id) if author_id else None

    counts = {
        "author_characters": 0,
        "author_characters_confirmed": 0,
        "author_pronunciations": 0,
    }
    if author_id:
        counts["author_characters"] = _count(
            db, select(func.count()).select_from(AuthorCharacter).where(AuthorCharacter.author_id == author_id),
        )
        counts["author_characters_confirmed"] = _count(
            db, select(func.count()).select_from(AuthorCharacter)
            .where(AuthorCharacter.author_id == author_id, AuthorCharacter.status == "confirmed"),
        )
        counts["author_pronunciations"] = _count(
            db, select(func.count()).select_from(AuthorPronunciation).where(AuthorPronunciation.author_id == author_id),
        )
    counts["linked_characters"] = _count(
        db, select(func.count()).select_from(Character)
        .where(Character.book_id == book.id, Character.author_character_id != ""),
    )
    counts["book_pronunciation_terms"] = len(_parse_pronunciation_notes(book.pronunciation_notes or ""))
    counts.update(_v2_counts(db, book.id))

    return {
        "book": {
            "id": book.id,
            "title": _book_title(book),
            "pipeline_mode": str(book.pipeline_mode or ""),
            "validation_profile": str(book.validation_profile or ""),
            "author_id": author_id,
        },
        "author": (
            {"id": author.id, "name": str(author.name or ""), "slug": str(author.slug or "")}
            if author is not None else None
        ),
        # Every author, so the book can be pointed at one from the profile panel.
        "authors": [
            {"id": row.id, "name": str(row.name or "")}
            for row in db.query(Author).order_by(Author.name.asc()).all()
        ],
        "suggested": None if author_id else _suggest_author(db, book.id),
        "counts": counts,
        "last_sync": _last_intervention(db, book.id, SYNC_ACTION),
    }


def sync_to_author(db, book, actor_uid: str, actor_name: str = "") -> dict:
    """Book → profile (the profile wins on conflicts), with an audit row."""
    summary = sync_book_to_author_profile(db, book)
    record_operator_intervention(
        db, book=book, action_type=SYNC_ACTION, actor_uid=actor_uid, actor_name=actor_name,
        reason="v2: книга → профиль автора", payload=summary,
    )
    return summary


def apply_from_author(db, book, *, overwrite: bool, actor_uid: str, actor_name: str = "") -> dict:
    """Profile → book (only blanks unless `overwrite`), with an audit row."""
    summary = apply_author_profile_to_book(db, book, overwrite=bool(overwrite))
    summary = {**summary, "overwrite": bool(overwrite)}
    record_operator_intervention(
        db, book=book, action_type=APPLY_ACTION, actor_uid=actor_uid, actor_name=actor_name,
        reason="v2: профиль автора → книга", payload=summary,
    )
    return summary
