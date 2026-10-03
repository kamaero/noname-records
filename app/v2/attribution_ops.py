"""An operator says who speaks in one segment, and that becomes the newest version.

This is the v2 replacement for the old validation editor's «переназначить реплику»:
no text is rewritten and no chapter is re-run. The reader sends the spans it wants
for a segment (whole paragraph, or two pieces when a replica sits inside narration);
they are checked against the segment's own text and the book's cast, stored as the
next version with `source="operator"`, and recorded in the operator journal so the
question «who changed this» has the same answer for v1 and v2.
"""
from __future__ import annotations

from app.models import Character, ScriptBook
from app.v2.models import V2Segment
from app.v2.store import record_operator_intervention, store_attributions

NARRATOR = "Рассказчик"
UNSURE = "UNSURE"


class ReassignError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _cast_names(db, book_id: str) -> dict[str, str]:
    """Lowercased name or alias → canonical character name for the book."""
    names: dict[str, str] = {}
    for character in db.query(Character).filter(Character.book_id == book_id).all():
        name = (character.name or "").strip()
        if not name or name.upper().startswith("UNSURE"):
            continue
        names[name.lower()] = name
        for alias in (character.aliases or "").split(","):
            alias = alias.strip()
            if alias:
                names.setdefault(alias.lower(), name)
    return names


def normalise_spans(raw, *, text_length: int) -> list[tuple[int, int, str, float]]:
    """Client spans into sorted, clipped, non-overlapping `(start, end, speaker, confidence)`.

    An empty list means «the whole segment to one speaker» only when exactly one
    speaker is given — the caller handles that case; here empty is an error.
    """
    if not isinstance(raw, list) or not raw:
        raise ReassignError("spans_required")
    spans: list[tuple[int, int, str, float]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ReassignError("span_not_object")
        speaker = str(entry.get("speaker") or "").strip()
        if not speaker:
            raise ReassignError("speaker_required")
        try:
            start = int(entry.get("start", 0))
            end = int(entry.get("end", text_length))
        except (TypeError, ValueError):
            raise ReassignError("bad_offsets") from None
        start, end = max(0, start), min(text_length, end)
        if end <= start:
            raise ReassignError("empty_span")
        try:
            confidence = float(entry.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        spans.append((start, end, speaker, confidence))
    spans.sort(key=lambda item: item[0])
    for (_, previous_end, _, _), (start, _, _, _) in zip(spans, spans[1:]):
        if start < previous_end:
            raise ReassignError("spans_overlap")
    return spans


def reassign_segment(db, *, segment_id: str, spans, actor_uid: str, actor_name: str = "",
                     keep_source: dict[tuple[int, int], str] | None = None) -> dict:
    """Оператор назначает спикеров для отрезка; роли проверены по касту и сохранены как новую версию.

    `keep_source` — только для серверного кода (приём находки консилиума): отрезки с
    этими границами сохраняют прежний источник. Полем в теле запроса это быть не может —
    клиент выдал бы правку человека за модельную.
    """
    segment = db.get(V2Segment, str(segment_id or "").strip())
    if segment is None:
        raise ReassignError("segment_not_found")
    cast = _cast_names(db, segment.book_id)
    checked = normalise_spans(spans, text_length=len(segment.text or ""))
    kept = dict(keep_source or {})

    records = []
    for start, end, speaker, confidence in checked:
        canonical = speaker
        if speaker not in (NARRATOR, UNSURE):
            canonical = cast.get(speaker.lower())
            if canonical is None:
                raise ReassignError("unknown_speaker")
        records.append({
            "unit_id": segment.id,
            "span_start": start,
            "span_end": end,
            "speaker": canonical,
            "confidence": confidence,
            "source": str(kept.get((start, end)) or "operator"),
        })
    stored = store_attributions(db, records)

    book = db.get(ScriptBook, segment.book_id)
    if book is not None:
        record_operator_intervention(
            db, book=book, action_type="v2_reassign_speaker", actor_uid=actor_uid, actor_name=actor_name,
            payload={"segment_id": segment.id, "spans": [
                {"start": r["span_start"], "end": r["span_end"], "speaker": r["speaker"]} for r in records
            ]},
        )
    return {"segment_id": segment.id, "spans": len(records), "rows": stored}
