"""Deterministic triage of suspect speaker-cue lines (Attribution Auditor v0, no LLM)."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

_CUE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*[—–-]")
# Intentionally minimal: only the Latin letters seen in real mojibake of cast names
# (one name typed in Latin lookalikes, e.g. "Zvezdil"/"Звездиl"). Unmapped Latin is left as-is, which is safe — such labels
# still carry Latin in the original and are flagged via the has_latin check in classify_cue.
_TRANSLIT = {"d": "д", "z": "з", "i": "и", "m": "м", "v": "в", "e": "е", "l": "л"}


@dataclass
class SuspectLine:
    chapter_index: int
    line_index: int
    line_hash: str
    current_speaker: str
    signal: str          # garbled_label | unsure_marker | unknown_speaker
    decision_class: str  # spec §5 class


_SIGNAL_TO_CLASS = {
    "garbled_label": "speaker_not_in_map",
    "unsure_marker": "resolve_unsure",
    "unsure_in_body": "resolve_unsure",
    "unknown_speaker": "speaker_not_in_map",
}

# An unresolved-speaker marker can sit inside the spoken text instead of in the
# speaker label, leaving a line whose label is a real cast member and therefore
# looks clean. Matched by marker name, not by "any bracket": the author's own
# «[цензура]» appears a dozen times in the source and is not a suspect.
_BODY_MARKER_RE = re.compile(r"\[\s*(?:UNSURE|UNKNOWN)\b", re.IGNORECASE)


def normalize_label(label: str) -> str:
    """Fold a speaker label for comparison against the approved cast.

    Drops the acute and grave only. Stripping every combining mark would decompose
    «й» to «и» + breve and merge «Тайн» with «Таин» — distinct characters, and this
    key decides which cast member a line belongs to.
    """
    s = unicodedata.normalize("NFD", label or "")
    s = s.replace("\u0301", "").replace("\u0300", "")
    s = unicodedata.normalize("NFC", s)
    s = s.strip().lower()
    return "".join(_TRANSLIT.get(ch, ch) for ch in s)


def line_hash(line: str) -> str:
    return "sha256:" + hashlib.sha256(line.encode("utf-8")).hexdigest()[:16]


def classify_cue(label: str, approved_norms: set[str]) -> str:
    has_latin = bool(re.search(r"[A-Za-z]", label))
    if label.startswith("UNSURE"):
        return "unsure_marker"
    if normalize_label(label) in approved_norms:
        return "garbled_label" if has_latin else "clean"
    return "garbled_label" if has_latin else "unknown_speaker"


def triage_chapter(*, chapter_index: int, fountain_text: str, approved_norms: set[str]) -> list[SuspectLine]:
    out: list[SuspectLine] = []
    for idx, raw in enumerate((fountain_text or "").splitlines()):
        m = _CUE_RE.match(raw)
        if m is None:
            continue
        label = m.group(1).strip()
        signal = classify_cue(label, approved_norms)
        if signal == "clean":
            if not _BODY_MARKER_RE.search(raw[m.end():]):
                continue
            signal = "unsure_in_body"
        out.append(SuspectLine(
            chapter_index=chapter_index,
            line_index=idx,
            line_hash=line_hash(raw),
            current_speaker=label,
            signal=signal,
            decision_class=_SIGNAL_TO_CLASS[signal],
        ))
    return out
