"""Ask a model who speaks, and believe only the checkable parts of the answer.

The model annotates rather than rewrites, so its answer is small and every claim in it
can be checked against the text already held: a span must lie inside its segment,
spans of one segment must not overlap, and a speaker must be someone the cast knows —
or «Рассказчик», or «UNSURE» when the model declines to say.

Nothing here raises. The old pipeline's habit was to throw from deep inside one part
and degrade the whole chapter to its draft; here a segment the model got wrong about
becomes UNSURE with confidence zero, the problem is recorded, and the rest stands.
Silence about a segment is treated the same way — the benchmark counts uncovered text
against the model, so a gap has to be visible rather than absent.

The model is not asked for offsets. Counting characters is what language models do
worst, and an answer of «start 58, end 140» cannot be checked except by trusting it.
It is asked for one dominant speaker per paragraph and, only when a paragraph really
does change voice, for the verbatim quote of each part. A quote either is found in
the text or is not — and when it is not, the paragraph falls back to its dominant
speaker with the failure on record, instead of a span landing on the wrong words.

The model itself is a callable handed in by the caller, so everything below runs in
tests against a fake; `app.v2.llm` makes the real one.
"""
from __future__ import annotations

import json
import re
from typing import Callable

NARRATOR = "Рассказчик"
UNSURE = "UNSURE"

# What one call returns: the raw `call_chat` dict — `content` (JSON text) and `usage`.
Llm = Callable[[str, str, dict], dict]

SOURCE_MAIN = "llm"
SOURCE_REVIEW = "llm_review"

SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "speaker": {"type": "string"},
                    "confidence": {"type": "number"},
                    "parts": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "speaker": {"type": "string"},
                                "quote": {"type": "string"},
                            },
                            "required": ["speaker", "quote"],
                        },
                    },
                },
                "required": ["id", "speaker", "confidence"],
            },
        }
    },
    "required": ["items"],
}


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _spans_of(item, unit, cast, problems) -> list[dict]:
    raw = item.get("spans") if isinstance(item, dict) else None
    if not isinstance(raw, list) or not raw:
        problems.append(f"{unit.id}: ответ без спанов")
        return [unit.as_record(span_start=0, span_end=len(unit.text), speaker=UNSURE)]

    records: list[dict] = []
    cursor = 0
    for entry in raw:
        if not isinstance(entry, dict):
            problems.append(f"{unit.id}: спан не объект")
            continue

        start = max(0, _as_int(entry.get("start")))
        end = _as_int(entry.get("end"), default=len(unit.text))
        if end > len(unit.text):
            problems.append(f"{unit.id}: спан за концом текста, clipped {end}->{len(unit.text)}")
            end = len(unit.text)
        # Spans arrive in reading order; one that starts inside the previous is a
        # contradiction about the same words, so the earlier claim stands.
        if start < cursor:
            problems.append(f"{unit.id}: overlap {start}<{cursor}, начало сдвинуто")
            start = cursor
        if end <= start:
            continue

        speaker = str(entry.get("speaker") or "").strip()
        confidence = _as_float(entry.get("confidence"))
        if speaker != NARRATOR and speaker != UNSURE and speaker not in cast:
            problems.append(f"{unit.id}: каст не знает {speaker!r}")
            speaker, confidence = UNSURE, 0.0

        records.append(unit.as_record(
            span_start=start, span_end=end, speaker=speaker, confidence=confidence,
        ))
        cursor = end

    if not records:
        return [unit.as_record(span_start=0, span_end=len(unit.text), speaker=UNSURE)]
    return records


def parse_response(payload, units, *, cast) -> tuple[list[dict], list[str]]:
    """`(records, problems)` — one record per attributed span, and what went wrong."""
    problems: list[str] = []
    by_id = {unit.id: unit for unit in units}

    items = (payload or {}).get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        problems.append("ответ без списка items")
        items = []

    answered: dict[str, list[dict]] = {}
    for item in items:
        if not isinstance(item, dict):
            problems.append("элемент ответа не объект")
            continue
        unit_id = str(item.get("id") or "")
        unit = by_id.get(unit_id)
        if unit is None:
            problems.append(f"{unit_id or '<без id>'}: такого сегмента в порции не было")
            continue
        answered[unit_id] = _spans_of(item, unit, cast, problems)

    records: list[dict] = []
    for unit in units:
        if unit.id in answered:
            records.extend(answered[unit.id])
            continue
        problems.append(f"{unit.id}: модель промолчала")
        records.append(unit.as_record(span_start=0, span_end=len(unit.text), speaker=UNSURE))
    return records, problems


# --- locating quotes ------------------------------------------------------------

# The model is allowed to be sloppy about typography, not about words. Every kind of
# dash becomes one dash, every kind of quote mark one quote, runs of whitespace one
# space, and accents are dropped: «Дгарни́н» in the text and «Дгарнин» in the answer
# are the same name. Both sides go through the same fold, and offsets are kept for
# the text side so a match maps back to the author's own characters.
_DASHES = set("-–—‑−")
_QUOTES = set("«»“”„‟\"")
_COMBINING = re.compile("[\u0300-\u036f]")


def _fold_char(ch: str) -> str:
    if ch in _DASHES:
        return "-"
    if ch in _QUOTES:
        return '"'
    lowered = ch.lower()
    return lowered if len(lowered) == 1 else ch


def _normalise(text: str) -> tuple[str, list[int]]:
    """`(folded, offsets)` — folded text, and for each folded char its offset in `text`."""
    out: list[str] = []
    offsets: list[int] = []
    pending_space = False
    for index, ch in enumerate(text or ""):
        if _COMBINING.match(ch):
            continue
        if ch.isspace():
            pending_space = bool(out)
            continue
        if pending_space:
            out.append(" ")
            offsets.append(index - 1)
            pending_space = False
        out.append(_fold_char(ch))
        offsets.append(index)
    return "".join(out), offsets


_HEX_PAIR = re.compile(r"[\x00-\x1f][0-9a-fA-F]{2}")


def repair_escaped_name(value: str) -> str:
    """Put back a name that arrived as a half-decoded `\\uXXXX` escape.

    Some providers answer with the name escaped, and somewhere on the way `\\u04`
    is read as a two-digit escape: «Маркиз» comes back as chr(0x04) + "1c" + … —
    the high byte of each codepoint as a control character, its low byte as the two
    characters behind it. Reassembling is exact; a control character with nothing
    readable behind it is dropped, because a name with control characters in it is
    not a name and must not reach the cast.
    """
    text = str(value or "")
    if not text or all(ord(ch) >= 32 for ch in text):
        return text
    out = _HEX_PAIR.sub(lambda m: chr(ord(m.group(0)[0]) * 256 + int(m.group(0)[1:], 16)), text)
    return "".join(ch for ch in out if ord(ch) >= 32).strip()


def _span(start: int, end: int, speaker: str, confidence: float) -> dict:
    return {"start": start, "end": end, "speaker": speaker, "confidence": confidence}


def parts_to_spans(item: dict, unit, problems: list[str]) -> dict:
    """The `speaker/parts` answer for one unit into the `spans` shape `parse_response` reads.

    Parts are located one after another from a moving cursor, so a quote that appears
    twice lands on the occurrence that follows the previous part. Text between located
    parts belongs to the dominant speaker; whitespace-only gaps are folded into the
    preceding span rather than becoming a sliver of somebody else's. Any quote that
    cannot be found sends the whole paragraph to its dominant speaker, with a note.
    """
    text = unit.text
    speaker = repair_escaped_name(str(item.get("speaker") or "").strip())
    confidence = _as_float(item.get("confidence"))
    whole = {"id": item.get("id"), "spans": [_span(0, len(text), speaker, confidence)]}

    parts = item.get("parts")
    if not isinstance(parts, list) or not parts:
        return whole

    folded, offsets = _normalise(text)
    located: list[tuple[int, int, str]] = []
    cursor = 0
    for part in parts:
        if not isinstance(part, dict):
            problems.append(f"{unit.id}: часть не объект, абзац целиком отдан {speaker!r}")
            return whole
        quote = str(part.get("quote") or "")
        needle, _ = _normalise(quote)
        if not needle:
            continue
        position = folded.find(needle, cursor)
        if position < 0:
            problems.append(f"{unit.id}: цитата не найдена, абзац целиком отдан {speaker!r}: {quote[:60]!r}")
            return whole
        start = offsets[position]
        end = offsets[position + len(needle) - 1] + 1
        located.append((start, end, repair_escaped_name(str(part.get("speaker") or speaker).strip()) or speaker))
        cursor = position + len(needle)

    if not located:
        return whole

    spans: list[dict] = []
    position = 0
    for start, end, part_speaker in located:
        if start > position:
            if text[position:start].strip():
                spans.append(_span(position, start, speaker, confidence))
            elif spans:
                spans[-1]["end"] = start
            else:
                start = position
        spans.append(_span(start, end, part_speaker, confidence))
        position = end
    if position < len(text):
        if text[position:].strip():
            spans.append(_span(position, len(text), speaker, confidence))
        else:
            spans[-1]["end"] = len(text)

    merged: list[dict] = []
    for span in spans:
        if merged and merged[-1]["speaker"] == span["speaker"] and merged[-1]["end"] == span["start"]:
            merged[-1]["end"] = span["end"]
        else:
            merged.append(span)
    return {"id": item.get("id"), "spans": merged}


# --- batches and prompts --------------------------------------------------------


def build_batches(units, *, max_units: int = 50, max_chars: int = 12000) -> list[list]:
    """Consecutive runs of units, each under both caps; an oversized unit goes alone."""
    batches: list[list] = []
    current: list = []
    chars = 0
    for unit in units:
        length = len(unit.text)
        if current and (len(current) >= max_units or chars + length > max_chars):
            batches.append(current)
            current, chars = [], 0
        current.append(unit)
        chars += length
    if current:
        batches.append(current)
    return batches


def _token(unit) -> str:
    return f"#{unit.ordinal:05d}"


def _flat(text: str) -> str:
    return " ".join((text or "").split())


def _cast_block(cast_lines: list[str]) -> list[str]:
    lines = ["ПЕРСОНАЖИ:"]
    lines.extend(cast_lines or ["- (список персонажей пуст)"])
    return lines


CONTEXT_SIZE = 6


def render_user_prompt(batch, *, cast_lines: list[str], context) -> str:
    """Cast, then the tail of what was already decided, then what to decide now."""
    lines = _cast_block(cast_lines)
    shown = list(context)[-CONTEXT_SIZE:]
    if shown:
        lines += ["", "КОНТЕКСТ (уже размечено, не отвечать):"]
        lines += [f"{_token(unit)} [{speaker}] {_flat(unit.text)}" for unit, speaker in shown]
    lines += ["", "РАЗМЕТИТЬ:"]
    lines += [f"{_token(unit)} {_flat(unit.text)}" for unit in batch]
    return "\n".join(lines)


def _dominant(records: list[dict]) -> tuple[str, float]:
    """The speaker holding the most characters of a unit, and the unit's weakest confidence."""
    if not records:
        return UNSURE, 0.0
    widest = max(records, key=lambda r: r["span_end"] - r["span_start"])
    return widest["speaker"], min(_as_float(r.get("confidence")) for r in records)


# --- asking -----------------------------------------------------------------------


def new_stats() -> dict:
    return {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def _call(llm: Llm, system_prompt: str, user_prompt: str, stats: dict) -> dict:
    """One call, counted whether or not it comes back usable."""
    stats["calls"] += 1
    result = llm(system_prompt, user_prompt, SCHEMA)
    if isinstance(result, dict):
        usage = result.get("usage") or {}
        stats["prompt_tokens"] += _as_int(usage.get("prompt_tokens"))
        stats["completion_tokens"] += _as_int(usage.get("completion_tokens"))
        content = result.get("content")
    else:
        content = result
    payload = json.loads(content if isinstance(content, str) else json.dumps(content))
    if not isinstance(payload, dict):
        raise ValueError("ответ не JSON-объект")
    return payload


def _ask_twice(llm: Llm, system_prompt: str, user_prompt: str, stats: dict) -> tuple[dict | None, str | None]:
    """The batch, once; on a bad answer once more with the error spelled out. Never raises."""
    try:
        return _call(llm, system_prompt, user_prompt, stats), None
    except Exception as first:  # noqa: BLE001 — anything the model or transport threw
        note = (
            f"\n\nОШИБКА предыдущего ответа: {type(first).__name__}: {str(first)[:300]}\n"
            "Верни строго JSON по схеме, ровно за абзацы из блока РАЗМЕТИТЬ."
        )
        try:
            return _call(llm, system_prompt, user_prompt + note, stats), None
        except Exception as second:  # noqa: BLE001
            return None, f"{first} / {second}"


def _translate(payload: dict, targets, *, ignore: set[str], problems: list[str]) -> dict:
    """Model tokens back to unit ids, parts to spans; answers about `ignore` are dropped."""
    by_token = {_token(unit): unit for unit in targets}
    items: list = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            items.append(item)
            continue
        token = str(item.get("id") or "").strip()
        unit = by_token.get(token)
        if unit is None:
            if token not in ignore:
                items.append(item)
            continue
        items.append(parts_to_spans({**item, "id": unit.id}, unit, problems))
    return {"items": items}


def _accept(records: list[dict], cast, source: str) -> None:
    for record in records:
        speaker = record["speaker"]
        if speaker not in (NARRATOR, UNSURE) and isinstance(cast, dict):
            record["speaker"] = cast.get(speaker, speaker)
        record["source"] = source


def _system_prompt(given: str | None) -> str:
    if given:
        return given
    from app.v2.llm import load_system_prompt  # the only file-reading import, kept lazy

    return load_system_prompt()


def attribute_units(
    units,
    *,
    cast,
    cast_lines: list[str],
    llm: Llm,
    system_prompt: str | None = None,
    on_batch=None,
    max_units: int = 50,
    max_chars: int = 12000,
) -> tuple[list[dict], list[str], dict]:
    """`(records, problems, stats)` for a chapter's units, batch by batch.

    Each batch sees the tail of the previous one with the speakers that were accepted
    for it, so a dialogue does not lose its thread at a batch boundary. A batch that
    fails twice becomes UNSURE and the next one is asked anyway.
    """
    system = _system_prompt(system_prompt)
    records: list[dict] = []
    problems: list[str] = []
    stats = new_stats()
    context: list[tuple] = []
    done = 0

    for batch in build_batches(units, max_units=max_units, max_chars=max_chars):
        shown = context[-CONTEXT_SIZE:]
        user = render_user_prompt(batch, cast_lines=cast_lines, context=shown)
        payload, error = _ask_twice(llm, system, user, stats)
        if payload is None:
            problems.append(f"{batch[0].id}..{batch[-1].id}: порция без ответа после повтора: {error}")
            batch_records = [unit.as_record(span_start=0, span_end=len(unit.text), speaker=UNSURE) for unit in batch]
        else:
            payload = _translate(payload, batch, ignore={_token(u) for u, _ in shown}, problems=problems)
            batch_records, batch_problems = parse_response(payload, batch, cast=cast)
            problems.extend(batch_problems)
        _accept(batch_records, cast, SOURCE_MAIN)
        records.extend(batch_records)

        by_unit: dict[str, list[dict]] = {}
        for record in batch_records:
            by_unit.setdefault(record["unit_id"], []).append(record)
        context = (context + [(unit, _dominant(by_unit.get(unit.id, []))[0]) for unit in batch])[-CONTEXT_SIZE:]

        done += len(batch)
        if on_batch:
            on_batch(done, len(units))

    return records, problems, stats


# --- second look ------------------------------------------------------------------


def render_review_prompt(targets, units, by_unit: dict, *, cast_lines: list[str], radius: int) -> str:
    """Each target inside its neighbours, all shown with what the first pass decided."""
    index = {unit.id: position for position, unit in enumerate(units)}
    target_ids = {unit.id for unit in targets}
    window: set[int] = set()
    for unit in targets:
        centre = index[unit.id]
        window.update(range(max(0, centre - radius), min(len(units), centre + radius + 1)))

    lines = _cast_block(cast_lines)
    lines += ["", "ОКНО (соседи с текущей разметкой; абзацы с пометкой → ПЕРЕСМОТРЕТЬ решить заново, за остальные не отвечать):"]
    previous = None
    for position in sorted(window):
        if previous is not None and position != previous + 1:
            lines.append("…")
        unit = units[position]
        speaker, _ = _dominant(by_unit.get(unit.id, []))
        line = f"{_token(unit)} [{speaker}] {_flat(unit.text)}"
        if unit.id in target_ids:
            line += "   → ПЕРЕСМОТРЕТЬ"
        lines.append(line)
        previous = position
    lines += ["", "РАЗМЕТИТЬ (ответить только за эти абзацы):"]
    lines += [f"{_token(unit)} {_flat(unit.text)}" for unit in targets]
    return "\n".join(lines)


def review_low_confidence(
    records: list[dict],
    units,
    *,
    threshold: float = 0.7,
    radius: int = 8,
    cast,
    cast_lines: list[str],
    llm: Llm,
    system_prompt: str | None = None,
    max_targets: int = 20,
) -> tuple[list[dict], list[str], dict]:
    """Ask again about what came back weak, with more room around it.

    A unit is looked at again when any of its spans is under `threshold` or UNSURE.
    Whatever the model says this time replaces the first answer for that unit under
    `source="llm_review"`; what it stays silent about, and what fails twice, keeps
    the first answer — a second pass must not be able to lose ground.
    """
    problems: list[str] = []
    stats = new_stats()
    by_unit: dict[str, list[dict]] = {}
    for record in records:
        by_unit.setdefault(record["unit_id"], []).append(record)

    targets = []
    for unit in units:
        own = by_unit.get(unit.id)
        if not own:
            continue
        if any(r["speaker"] == UNSURE for r in own) or _dominant(own)[1] < threshold:
            targets.append(unit)
    if not targets:
        return list(records), problems, stats

    system = _system_prompt(system_prompt)
    replacements: dict[str, list[dict]] = {}
    for start in range(0, len(targets), max_targets):
        chunk = targets[start:start + max_targets]
        user = render_review_prompt(chunk, units, by_unit, cast_lines=cast_lines, radius=radius)
        payload, error = _ask_twice(llm, system, user, stats)
        if payload is None:
            problems.append(f"пересмотр {chunk[0].id}..{chunk[-1].id} без ответа после повтора, "
                            f"оставлена прежняя разметка: {error}")
            continue

        answered_tokens = {
            str(item.get("id") or "").strip() for item in payload.get("items") or [] if isinstance(item, dict)
        }
        answered = [unit for unit in chunk if _token(unit) in answered_tokens]
        for unit in chunk:
            if _token(unit) not in answered_tokens:
                problems.append(f"{unit.id}: пересмотр без ответа, оставлена прежняя разметка")
        window_tokens = {_token(unit) for unit in units}
        payload = _translate(payload, answered, ignore=window_tokens, problems=problems)
        new_records, new_problems = parse_response(payload, answered, cast=cast)
        problems.extend(new_problems)
        _accept(new_records, cast, SOURCE_REVIEW)
        for record in new_records:
            replacements.setdefault(record["unit_id"], []).append(record)

    out: list[dict] = []
    emitted: set[str] = set()
    for record in records:
        unit_id = record["unit_id"]
        if unit_id not in replacements:
            out.append(record)
        elif unit_id not in emitted:
            out.extend(replacements[unit_id])
            emitted.add(unit_id)
    return out, problems, stats
