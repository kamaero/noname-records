"""Assemble what an actor reads: a chapter's prose with its current annotations.

The reader never sees a version number. For every segment it gets the attribution
spans and stress marks of the highest version written for that segment — the
"effective" state — while older rows stay in the tables as history. Everything
here is plain queries and dict-building, so it runs against an in-memory sqlite in
tests and against the real database behind the API without knowing which.
"""
from __future__ import annotations

import collections

from sqlalchemy import and_, func, select

from app.models import BookBudget, Character, ScriptBook, ScriptChapter
from app.services.character_colors import build_character_style_maps, resolve_character_style
from app.v2.models import V2Attribution, V2Segment, V2StressMark
from app.v2.remark_candidates import remark_candidates
from app.v2.review_ops import is_approved
from app.v2.store import load_chapter_segments
from app.v2.word_rarity import is_rare

NARRATOR = "Рассказчик"
UNSURE = "UNSURE"

# sqlite caps bound parameters per statement; keep IN-lists comfortably below it.
_IN_CHUNK = 500


def _chunks(items: list[str]) -> list[list[str]]:
    return [items[i : i + _IN_CHUNK] for i in range(0, len(items), _IN_CHUNK)]


def _effective_rows(db, model, segment_ids, order_column) -> dict[str, list]:
    """Rows of `model` whose version is the max for their segment, grouped by segment."""
    ids = [str(item) for item in segment_ids if str(item or "").strip()]
    if not ids:
        return {}
    latest: dict[str, int] = {}
    rows: list = []
    for chunk in _chunks(ids):
        latest.update(
            dict(
                db.query(model.segment_id, func.max(model.version))
                .filter(model.segment_id.in_(chunk))
                .group_by(model.segment_id)
                .all()
            )
        )
        rows.extend(
            db.query(model)
            .filter(model.segment_id.in_(chunk))
            .order_by(model.segment_id.asc(), order_column.asc())
            .all()
        )
    out: dict[str, list] = collections.OrderedDict()
    for row in rows:
        if int(row.version or 0) == int(latest.get(row.segment_id) or 0):
            out.setdefault(row.segment_id, []).append(row)
    return out


def effective_attributions(db, segment_ids) -> dict[str, list[V2Attribution]]:
    return _effective_rows(db, V2Attribution, segment_ids, V2Attribution.span_start)


def effective_stress(db, segment_ids) -> dict[str, list[V2StressMark]]:
    return _effective_rows(db, V2StressMark, segment_ids, V2StressMark.word_start)


def _latest_versions_for_book(model, book_id: str):
    """Subquery: (segment_id, v) — the highest version of `model` per segment of the book."""
    segment_ids = select(V2Segment.id).where(V2Segment.book_id == book_id)
    return (
        select(model.segment_id, func.max(model.version).label("v"))
        .where(model.segment_id.in_(segment_ids))
        .group_by(model.segment_id)
        .subquery()
    )


def effective_book_rows(db, model, book_id: str, *columns) -> list:
    """Effective rows of `model` for a whole book, as plain tuples `(segment_id, *columns)`.

    The per-segment variant above takes the ids it is given; a book-wide tool (the
    cast) needs every segment at once. Joining on the max-version subquery keeps the
    filtering in SQL, and column tuples instead of ORM rows keep the Python side to
    one allocation per row. Fine for attributions (one or two per segment); stress
    marks run to hundreds of thousands per book and are counted, not fetched — see
    `app.v2.stress_ops`.
    """
    latest = _latest_versions_for_book(model, book_id)
    stmt = (
        select(model.segment_id, *columns)
        .join(latest, and_(latest.c.segment_id == model.segment_id, latest.c.v == model.version))
        .order_by(model.segment_id.asc())
    )
    return db.execute(stmt).all()


def effective_book_attributions(db, book_id: str) -> list:
    """`(segment_id, span_start, span_end, speaker, source)` for the book's effective spans."""
    return effective_book_rows(
        db, V2Attribution, book_id,
        V2Attribution.span_start, V2Attribution.span_end, V2Attribution.speaker, V2Attribution.source,
    )


def _chapters_with_v2(db, chapter_ids: list[str]) -> set[str]:
    found: set[str] = set()
    for chunk in _chunks(chapter_ids):
        found.update(
            str(row[0])
            for row in db.query(V2Segment.chapter_id).filter(V2Segment.chapter_id.in_(chunk)).distinct().all()
        )
    return found


def _book_title(book: ScriptBook | None) -> str:
    if book is None:
        return ""
    return str(book.display_title or book.title or "").strip()


def build_book_chapters(db, book_id: str) -> dict | None:
    """`{book, chapters}` for the rail and the entry page; None when the book is unknown."""
    book = db.get(ScriptBook, book_id)
    if book is None:
        return None
    chapters = (
        db.query(ScriptChapter)
        .filter(ScriptChapter.book_id == book.id)
        .order_by(ScriptChapter.chapter_index.asc())
        .all()
    )
    with_v2 = _chapters_with_v2(db, [row.id for row in chapters])
    return {
        "book": {"id": book.id, "title": _book_title(book)},
        "chapters": [
            {
                "id": row.id,
                "index": int(row.chapter_index or 0),
                "title": str(row.chapter_title or "").strip(),
                "has_v2": row.id in with_v2,
                "approved": is_approved(row),
            }
            for row in chapters
        ],
    }


def _narrator_actor(db, book_id: str, characters: list[Character]) -> str:
    for character in characters:
        if str(character.name or "").strip() == NARRATOR and str(character.actor_name or "").strip():
            return str(character.actor_name).strip()
    for character in characters:
        if str(character.narrator_role or "").strip() and str(character.actor_name or "").strip():
            return str(character.actor_name).strip()
    budget = db.query(BookBudget).filter(BookBudget.book_id == book_id).first()
    if budget is not None:
        return str(budget.narrator_actor_name or "").strip()
    return ""


def _build_cast(db, book_id: str, characters: list[Character], line_counts: dict[str, int]) -> list[dict]:
    bg_map, text_map, weight_map, style_map = build_character_style_maps(characters, extra_names=[NARRATOR, UNSURE])
    actors = {str(c.name or "").strip(): str(c.actor_name or "").strip() for c in characters if str(c.name or "").strip()}
    actors[NARRATOR] = _narrator_actor(db, book_id, characters)
    ids = {str(c.name or "").strip(): str(c.id or "") for c in characters if str(c.name or "").strip()}
    # what the character is and how the author wants him to sound: the dictor reads
    # this where he reads the lines, not on a screen he never opens
    facts = {
        str(c.name or "").strip(): " · ".join(
            part for part in (str(c.race or "").strip(), str(c.temperament or "").strip()) if part
        )
        for c in characters
        if str(c.name or "").strip()
    }
    notes = {str(c.name or "").strip(): str(getattr(c, "operator_note", "") or "").strip()
             for c in characters if str(c.name or "").strip()}

    def style_of(name: str) -> tuple[str, str, str, str]:
        if name in bg_map:
            return bg_map[name], text_map[name], weight_map[name], style_map[name]
        return resolve_character_style(name)

    def entry(name: str) -> dict:
        bg, fg, weight, style = style_of(name)
        return {
            "name": name,
            "character_id": ids.get(name, ""),
            "actor": actors.get(name, ""),
            "color": bg,
            "text_color": fg,
            "weight": weight,
            "font_style": style,
            "lines": int(line_counts.get(name, 0)),
            "about": facts.get(name, ""),
            "note": notes.get(name, ""),
        }

    others = sorted(
        (name for name in line_counts if name not in (NARRATOR, UNSURE)),
        key=lambda name: (-line_counts[name], name.lower()),
    )
    cast = [entry(NARRATOR)] + [entry(name) for name in others]
    if UNSURE in line_counts:
        cast.append(entry(UNSURE))
    return cast


def segments_payload(segments, attributions: dict, stress: dict, line_counts=None) -> list[dict]:
    """Segments as the reader draws them: text, attribution spans, stress marks.

    Shared by the chapter view and the role view, so a paragraph looks and behaves the
    same whether it is read inside its chapter or inside one actor's own list.
    `line_counts` collects lines per speaker for the caller that needs a legend.
    """
    out: list[dict] = []
    for segment in segments:
        spans = []
        counted: set[str] = set()  # a replica is a paragraph the role speaks in, torn or not
        for row in attributions.get(segment.id, []):
            speaker = str(row.speaker or "").strip() or UNSURE
            if line_counts is not None and speaker not in counted:
                counted.add(speaker)
                line_counts[speaker] += 1
            spans.append({
                "start": int(row.span_start or 0),
                "end": int(row.span_end or 0),
                "speaker": speaker,
                "confidence": float(row.confidence or 0.0),
                "source": str(row.source or ""),
            })
        text = str(segment.text or "")
        out.append({
            "id": segment.id,
            "ordinal": int(segment.ordinal or 0),
            "kind": str(segment.kind or "paragraph"),
            "text": text,
            "spans": spans,
            "stress": [
                {
                    "start": int(row.word_start or 0),
                    "end": int(row.word_end or 0),
                    "vowel": int(row.vowel_offset or 0),
                    "source": str(row.source or ""),
                    # «только редкие» in the reader: the mark a reader in flow may need
                    "rare": is_rare(text[int(row.word_start or 0) : int(row.word_end or 0)]),
                }
                for row in stress.get(segment.id, [])
            ],
            # Кандидаты в авторские ремарки для ручной подсветки в редакторе (см.
            # remark-picker): считаются по тем же действующим спанам, что и «spans»
            # выше, тем же путём, каким ударения уже едут готовым списком.
            "remark_candidates": [
                {"start": a, "end": b} for a, b in remark_candidates(text, spans)
            ],
        })
    return out


def build_chapter_payload(db, chapter_id: str, *, can_edit: bool = False, can_voice: bool = False) -> dict | None:
    """Everything the reader needs for one chapter; None when the chapter is unknown.

    `can_edit` is decided by the caller from the session (admin or author) and only
    passed through, so the page knows whether to show its editing controls.
    """
    chapter = db.get(ScriptChapter, chapter_id)
    if chapter is None:
        return None
    book = db.get(ScriptBook, chapter.book_id)
    book_id = str(chapter.book_id or "")

    listing = build_book_chapters(db, book_id) if book is not None else None
    chapters = listing["chapters"] if listing else []

    segments = load_chapter_segments(db, chapter_id=chapter_id)
    segment_ids = [segment.id for segment in segments]
    attributions = effective_attributions(db, segment_ids)
    stress = effective_stress(db, segment_ids)

    line_counts: dict[str, int] = collections.Counter()
    segment_payload = segments_payload(segments, attributions, stress, line_counts)

    # Colours last: build_character_style_maps fills defaults onto the ORM rows, and the
    # session is never committed here, so nothing of that reaches the database.
    characters = db.query(Character).filter(Character.book_id == book_id).all() if book_id else []
    cast = _build_cast(db, book_id, characters, dict(line_counts))

    return {
        "book": {"id": book_id, "title": _book_title(book)},
        "chapter": {
            "id": chapter.id,
            "index": int(chapter.chapter_index or 0),
            "title": str(chapter.chapter_title or "").strip(),
            "segments_count": len(segments),
            "attributed": bool(attributions),
            # the author's «проверено», the mark publishing waits for
            "approved": is_approved(chapter),
            "status": str(chapter.status or ""),
        },
        "chapters": chapters,
        "cast": cast,
        "segments": segment_payload,
        "can_edit": bool(can_edit),
        # stress and palette: a dictor's craft, and a narrower door than `can_edit`
        "can_voice": bool(can_voice or can_edit),
    }
