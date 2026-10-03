"""Price a v2 book: how much each role speaks, and what that costs.

v1 arrived at these numbers by re-reading the fountain text it had generated —
one line per `[Роль] — реплика`, words counted off the same string. v2 never
writes that text, so that path found nothing and every role in a fully attributed
book was worth zero rubles. Here the same two numbers come from the annotations
themselves: one line per effective attribution span, words from the span's own
slice of the immutable segment.

Pricing order is unchanged from v1 and deliberately explicit: a role's fixed price
wins, then its own rate, then the studio's default rate. What is *not* kept is v1's
habit of leaving a previous total standing when the count changed — a recompute
that cannot lower a number is not a recompute.
"""
from __future__ import annotations

import collections
import re

from sqlalchemy import func, select

from app.models import Character, CharacterBudgetSnapshot
from app.services.author_profile import normalize_name
from app.services.cast_budget import estimate_seconds_by_words
from app.services.shared_runtime import is_narrator_name
from app.services.studio_settings import default_rate_rub_per_min
from app.time_utils import utcnow_naive
from app.v2.cast_ops import is_placeholder_name, split_aliases
from app.v2.models import V2Attribution, V2Segment
from app.v2.reader import effective_book_attributions

_WORD = re.compile(r"\S+")


def _resolver(characters: list[Character]):
    """Speaker name (or alias, stress and case folded) → character id."""
    narrator = next((row for row in characters if is_narrator_name(row.name)), None)
    narrator_id = str(narrator.id) if narrator is not None else ""
    by_key: dict[str, str] = {}
    for row in characters:
        by_key.setdefault(normalize_name(row.name), str(row.id))
    for row in characters:
        for alias in split_aliases(row.aliases):
            by_key.setdefault(normalize_name(alias), str(row.id))

    def resolve(name: str) -> str | None:
        text = str(name or "").strip()
        if not text or is_placeholder_name(text):
            return None
        return narrator_id or None if is_narrator_name(text) else by_key.get(normalize_name(text))

    return resolve


def aggregate_v2_lines(db, book_id: str) -> dict[str, dict[str, int]]:
    """`character_id → {lines, words}` from the effective attributions of the book."""
    characters = [
        row for row in db.query(Character).filter(Character.book_id == book_id).all()
        if str(row.name or "").strip() and not is_placeholder_name(row.name)
    ]
    resolve = _resolver(characters)
    texts = dict(db.query(V2Segment.id, V2Segment.text).filter(V2Segment.book_id == book_id).all())

    known: dict[str, str | None] = {}
    counts: dict[str, dict[str, int]] = collections.defaultdict(lambda: {"lines": 0, "words": 0})
    # a replica is a paragraph the role speaks in; the words of every span are voiced and paid
    counted: set[tuple[str, str]] = set()
    for segment_id, start, end, speaker, _source in effective_book_attributions(db, book_id):
        name = str(speaker or "").strip()
        if name not in known:
            known[name] = resolve(name)
        target = known[name]
        if target is None:
            continue
        text = str(texts.get(str(segment_id)) or "")
        span = text[int(start or 0) : int(end or 0)] if text else ""
        if (target, str(segment_id)) not in counted:
            counted.add((target, str(segment_id)))
            counts[target]["lines"] += 1
        counts[target]["words"] += len(_WORD.findall(span))
    return dict(counts)


def price(lines: int, seconds: int, character: Character, rate: int) -> tuple[int, str]:
    """`(rubles, calc_mode)` — a fixed price, the role's own rate, or the studio's."""
    fixed = int(getattr(character, "manual_fixed_rub", 0) or 0)
    if fixed > 0:
        return fixed, "fixed"
    own = int(getattr(character, "manual_rate_rub_per_min", 0) or 0)
    per_minute = own if own > 0 else int(rate or 0)
    return int(round(per_minute * (max(0, int(seconds or 0)) / 60.0))), "rate_plan"


def rebuild_v2_budget(db, book_id: str, *, updated_by: str = "system") -> dict:
    """Rewrite every snapshot of the book from its v2 annotations. Idempotent."""
    counts = aggregate_v2_lines(db, book_id)
    characters = db.query(Character).filter(Character.book_id == book_id).all()
    snapshots = {
        str(row.character_id): row
        for row in db.query(CharacterBudgetSnapshot).filter(CharacterBudgetSnapshot.book_id == book_id).all()
    }
    # A snapshot whose character was merged away or deleted prices nobody. Left alone it
    # also keeps the book permanently «stale», because its timestamp never moves again.
    alive = {str(row.id) for row in characters}
    for character_id, snapshot in list(snapshots.items()):
        if character_id not in alive:
            db.delete(snapshot)
            snapshots.pop(character_id, None)
    rate = default_rate_rub_per_min(db)
    now = utcnow_naive()

    total = lines_total = 0
    for character in characters:
        data = counts.get(str(character.id), {"lines": 0, "words": 0})
        seconds = estimate_seconds_by_words(int(data["words"])) if data["words"] else 0
        rubles, mode = price(int(data["lines"]), seconds, character, rate)
        if not data["lines"] and mode != "fixed":
            rubles = 0
        snapshot = snapshots.get(str(character.id))
        if snapshot is None:
            snapshot = CharacterBudgetSnapshot(book_id=book_id, character_id=str(character.id))
            db.add(snapshot)
            snapshots[str(character.id)] = snapshot
        snapshot.lines_count = int(data["lines"])
        snapshot.approx_seconds = int(seconds)
        snapshot.calc_mode = mode
        snapshot.total_rub = int(rubles)
        snapshot.updated_at = now
        total += int(rubles)
        lines_total += int(data["lines"])

    db.flush()
    return {
        "characters": len(characters),
        "lines": lines_total,
        "total_rub": total,
        "rate_rub_per_min": rate,
        "updated_by": updated_by,
    }


def v2_budget_is_stale(db, book_id: str) -> bool:
    """True when the snapshots no longer answer for the markup as it stands now.

    Cheap enough to ask on every read of the cast screen: one count, one max, one
    min. It catches all three ways the numbers go out of date — a run that added
    attributions, an operator who moved a line to another role, and a role added to
    the cast after the last recompute.
    """
    segment_ids = select(V2Segment.id).where(V2Segment.book_id == book_id)
    latest = db.execute(
        select(func.max(V2Attribution.created_at)).where(V2Attribution.segment_id.in_(segment_ids))
    ).scalar()
    if latest is None:
        return False
    character_ids = select(Character.id).where(Character.book_id == book_id)
    characters = db.execute(
        select(func.count()).select_from(Character).where(Character.book_id == book_id)
    ).scalar() or 0
    # Only the snapshots that still belong to a living role count, in both questions:
    # an orphan row is deleted by the next rebuild, not evidence of a fresh one.
    priced = db.execute(
        select(func.count()).select_from(CharacterBudgetSnapshot)
        .where(CharacterBudgetSnapshot.book_id == book_id, CharacterBudgetSnapshot.character_id.in_(character_ids))
    ).scalar() or 0
    if priced < characters:
        return True
    oldest = db.execute(
        select(func.min(CharacterBudgetSnapshot.updated_at))
        .where(CharacterBudgetSnapshot.book_id == book_id, CharacterBudgetSnapshot.character_id.in_(character_ids))
    ).scalar()
    return oldest is None or latest > oldest
