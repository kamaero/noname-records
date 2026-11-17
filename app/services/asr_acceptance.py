"""Подтверждённый человеком вариант переживает ASR, пока неизменны звук и реплика."""
from __future__ import annotations

from copy import deepcopy
import math
import re


def _anchors(db, chapter_id, role):
    from app.services.asr_run import chapter_replicas, chapter_language
    from app.services.author_profile import normalize_name
    from app.services.speech_parts import speech_and_narration

    language = chapter_language(db, chapter_id)
    return [
        {"segment_id": row["segment_id"], "span_start": row["span_start"],
         "span_end": row["span_end"], "text": speech_and_narration(row["text"], language=language)[0]}
        for row in chapter_replicas(db, chapter_id)
        if normalize_name(row["role"]) == normalize_name(role)
        and speech_and_narration(row["text"], language=language)[0]
    ]


def _source(audio, chapter_id, role):
    duration = float(audio.duration_seconds or 0)
    if (not math.isfinite(duration) or duration <= 0 or audio.size_bytes <= 0
            or not re.fullmatch(r"[0-9a-f]{32}", str(audio.md5 or ""))):
        raise ValueError("owner_acceptance_source_invalid")
    if str(audio.role) != str(role):
        raise ValueError("owner_acceptance_role_changed")
    return {"audio_id": str(audio.id), "chapter_id": str(chapter_id), "role": str(role),
            "md5": str(audio.md5), "size_bytes": int(audio.size_bytes), "duration_seconds": duration}


def _takes(values, duration):
    if not isinstance(values, list) or not values:
        raise ValueError("owner_acceptance_takes_invalid")
    result = []
    previous = -1.0
    for value in values:
        if not isinstance(value, dict) or set(value) != {"start", "end"}:
            raise ValueError("owner_acceptance_takes_invalid")
        left, right = value["start"], value["end"]
        if (type(left) not in (int, float) or type(right) not in (int, float)
                or not math.isfinite(left) or not math.isfinite(right)
                or not 0 <= left < right <= duration or left < previous):
            raise ValueError("owner_acceptance_takes_invalid")
        result.append({"start": float(left), "end": float(right)})
        previous = right
    return result


def _validated(db, audio, chapter_id, role, expected, acceptance):
    from app.services.asr_run import find_chapter_for_take

    chapter = find_chapter_for_take(db, audio)
    if chapter is None or str(chapter.id) != str(chapter_id):
        raise ValueError("owner_acceptance_chapter_changed")
    source = _source(audio, chapter_id, role)
    if (not isinstance(acceptance, dict) or acceptance.get("version") != 1
            or acceptance.get("source") != source
            or not isinstance(acceptance.get("reviewed_by"), str) or not acceptance["reviewed_by"].strip()
            or not isinstance(acceptance.get("reason"), str) or not acceptance["reason"].strip()):
        raise ValueError("owner_acceptance_changed")
    anchors = _anchors(db, chapter_id, role)
    if [anchor["text"] for anchor in anchors] != expected:
        raise ValueError("owner_acceptance_script_changed")
    entries = acceptance.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("owner_acceptance_entries_invalid")
    selected, intervals = {}, []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("owner_acceptance_entries_invalid")
        matches = [i for i, anchor in enumerate(anchors) if anchor == entry.get("anchor")]
        if len(matches) != 1 or matches[0] in selected:
            raise ValueError("owner_acceptance_anchor_changed")
        if (entry.get("kind") not in {"variant", "vocalization"}
                or not isinstance(entry.get("observed_text"), str) or not entry["observed_text"].strip()):
            raise ValueError("owner_acceptance_entries_invalid")
        takes = _takes(entry.get("takes"), source["duration_seconds"])
        selected[matches[0]] = {**entry, "takes": takes}
        intervals.extend((take["start"], take["end"]) for take in takes)
    intervals.sort()
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
        raise ValueError("owner_acceptance_takes_overlap")
    return selected


def approve_heard(db, audio, heard, entries, *, reviewed_by, reason):
    """Отдельная операторская операция: исходный ответ ASR остаётся неизменным."""
    from app.services.asr_run import find_chapter_for_take, expected_lines

    chapter = find_chapter_for_take(db, audio)
    if chapter is None:
        raise ValueError("owner_acceptance_chapter_missing")
    anchors = _anchors(db, chapter.id, audio.role)
    decisions = []
    for entry in entries:
        index = entry.get("line_index")
        if type(index) is not int or not 0 <= index < len(anchors):
            raise ValueError("owner_acceptance_line_invalid")
        decisions.append({"anchor": anchors[index], "observed_text": entry.get("observed_text"),
                          "kind": entry.get("kind"), "takes": entry.get("takes")})
    acceptance = {"version": 1, "source": _source(audio, chapter.id, audio.role),
                  "reviewed_by": reviewed_by, "reason": reason, "entries": decisions}
    _validated(db, audio, chapter.id, audio.role, expected_lines(db, chapter.id, audio.role), acceptance)
    result = deepcopy(heard)
    result["owner_acceptance"] = acceptance
    return result


def apply_approval(db, audio, chapter_id, role, expected, alignment, heard):
    acceptance = heard.get("owner_acceptance")
    if acceptance is None:
        return alignment
    if alignment.get("binding_stale"):
        return {**alignment, "owner_acceptance_stale": True}
    try:
        selected = _validated(db, audio, chapter_id, role, expected, acceptance)
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError):
        # После замены файла или сценария старое «принято» не переносится на новый материал.
        return {**alignment, "owner_acceptance_stale": True}
    result = deepcopy(alignment)
    for index, entry in selected.items():
        line = result["lines"][index]
        takes = [{**take, "score": 0.0, "accepted_by_owner": True} for take in entry["takes"]]
        # Нулевой ASR-score сохраняет различие между совпадением слов и решением человека.
        line.update(matched=True, start=takes[0]["start"], end=takes[0]["end"], takes=takes,
                    accepted_by_owner=True, observed_text=entry["observed_text"], acceptance_kind=entry["kind"])
    result["missing"] = [i for i, line in enumerate(result["lines"]) if not line["matched"]]
    result["matched"] = len(expected) - len(result["missing"])
    result["coverage"] = result["matched"] / len(expected) if expected else 0.0
    return result
