"""Merge N per-part model answers into ONE valid script document.

FINAL splits a chapter into parts, and each part returns a complete fragment —
including its own `[CAST]` block. Joining the parts end to end therefore produced a
document with N cast blocks scattered through the body, which is not a shape any
consumer expects. `_repair_cast_from_dialogues` treats everything after the FIRST
`[CAST]` as body, so entries from the other blocks looked like "stray cast entries"
and were deleted — along with any real text the model happened to write with the cast
separator `::` instead of the dialogue dash.

Measured across «Крылья полумрака» (75 final artifacts, 231 cast blocks):

    1550 `::` lines whose text is NOT in the book  -> genuine character descriptions
     725 `::` lines starting with a dialogue dash  -> replies
     303 `::` lines with no dash but IN the book   -> the author's narration

1028 lines of the author's own prose were being destroyed. Position does not separate
them (replies appear inside a cast run), blank lines do not (17 of 231 blocks contain
one), and the leading dash misses all 303 narration lines.

What does separate them is the source text: the model INVENTS descriptions and COPIES
prose. A `::` line whose words appear in the chapter source is the author's text and
must be recovered; one whose words do not is a description and belongs in the cast.

`_repair_cast_from_dialogues` is deliberately left alone. It behaves correctly on a
well-formed document; this module's job is to hand it one.
"""
from __future__ import annotations

import re
import unicodedata

CAST_ENTRY_RE = re.compile(r"^\s*\[([^\]]{1,120})\]\s*::\s*(.*)$")
CAST_HEADER = "[CAST]"
_LEADING_DASH_RE = re.compile(r"^\s*[—–-]\s")
_WORD_RE = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё́-]*")

# How many leading words of a line to look for in the source. Long enough that a
# coincidence is implausible, short enough to survive the stress marks and small
# punctuation edits FINAL introduces.
_PROBE_WORDS = 6


def _fold(text: str) -> str:
    """Normalise for comparison: drop stress marks, case, and the ё/е distinction.

    Only the acute and grave are dropped, not every combining mark. In NFD «й» is
    «и» + breve, so stripping all marks turns «Тайн» into «таин» — and this function
    also deduplicates cast names, where that would merge two characters into one.
    The ё/е fold is deliberate and separate: the stress pass often writes one for the
    other, and they are the same word.
    """
    stripped = unicodedata.normalize("NFD", text or "")
    stripped = stripped.replace("\u0301", "").replace("\u0300", "")
    stripped = unicodedata.normalize("NFC", stripped)
    return stripped.lower().replace("ё", "е")


def build_source_index(source_text: str) -> str:
    """A normalised word stream of the chapter source, for substring probing."""
    return " ".join(_fold(w) for w in _WORD_RE.findall(source_text or ""))


def _is_from_source(payload: str, source_index: str) -> bool:
    if not source_index:
        return False
    words = [_fold(w) for w in _WORD_RE.findall(payload or "")]
    if len(words) < 4:
        # Too short to identify: two or three common words match almost anything.
        return False
    return " ".join(words[:_PROBE_WORDS]) in source_index


def _normalize_name(name: str) -> str:
    """Fold a character name for deduplication.

    «Дгарни́н» and «Дгарнин» are one character; merging must not make two of them.
    """
    return _fold(name).strip()


def split_cast_block(text: str) -> tuple[list[tuple[str, str]], str]:
    """Split a part into (cast-shaped entries, body).

    Everything from the `[CAST]` header up to the first non-blank line that is not
    cast-shaped is collected as entries — blank lines inside the run are skipped,
    because 17 of 231 real blocks contain them. Telling a description from a reply is
    NOT done here; that needs the source text and happens in merge_parts.
    """
    lines = (text or "").split("\n")
    header_at = None
    for idx, line in enumerate(lines):
        if line.strip() == CAST_HEADER:
            header_at = idx
            break
        if line.strip():
            break
    if header_at is None:
        return [], text or ""

    entries: list[tuple[str, str]] = []
    cursor = header_at + 1
    while cursor < len(lines):
        stripped = lines[cursor].strip()
        if not stripped:
            cursor += 1
            continue
        match = CAST_ENTRY_RE.match(stripped)
        if not match:
            break
        entries.append((match.group(1).strip(), match.group(2).strip()))
        cursor += 1

    body_lines = lines[:header_at] + lines[cursor:]
    return entries, "\n".join(body_lines).strip("\n")


def _recover_line(name: str, payload: str) -> str:
    """Turn one piece of the author's text back into a proper script line.

    A reply keeps its speaker and only swaps the separator. Narration loses the label
    entirely: the narrator's actor reads prose that looks exactly like the book, with
    nothing set apart.

    The text after the separator is copied verbatim in both cases. A reply carrying
    the author's words inside it — "- Привет! … - сказала девочка … - Мне 120 лет." —
    stays ONE line; splitting it at the inner dashes is never correct. The colouring
    of speech vs narration inside such a line is derived later, by
    _infer_line_segments_from_dialogue.
    """
    if _LEADING_DASH_RE.match(payload):
        return f"[{name}] — {payload}"
    return payload


def merge_parts(parts: list[str], source_text: str = "") -> tuple[str, dict]:
    """Merge per-part answers into one document: a single [CAST], then the bodies.

    Without `source_text` nothing is reclassified — guessing risks turning a real
    character description into spoken prose, which is worse than leaving it be.

    Returns (merged_text, report). The report is not decoration: without it nobody can
    see how much the merge is doing, which is exactly how a 50% text loss stayed
    invisible for three months.
    """
    source_index = build_source_index(source_text)
    cast_by_key: dict[str, tuple[str, str]] = {}
    bodies: list[str] = []
    report = {
        "parts": 0,
        "cast_blocks": 0,
        "cast_entries_seen": 0,
        "cast_entries_merged": 0,
        "replies_recovered": 0,
        "narration_recovered": 0,
    }

    for part in parts or []:
        if not (part or "").strip():
            continue
        report["parts"] += 1
        entries, body = split_cast_block(part)
        if entries:
            report["cast_blocks"] += 1

        recovered_from_block: list[str] = []
        for name, payload in entries:
            report["cast_entries_seen"] += 1
            if _is_from_source(payload, source_index):
                line = _recover_line(name, payload)
                if line.startswith("["):
                    report["replies_recovered"] += 1
                else:
                    report["narration_recovered"] += 1
                recovered_from_block.append(line)
                continue
            key = _normalize_name(name)
            if not key:
                continue
            existing = cast_by_key.get(key)
            # Keep the richer description: it is what the voice actor reads.
            if existing is None or len(payload) > len(existing[1]):
                cast_by_key[key] = (existing[0] if existing else name, payload)

        body_lines: list[str] = []
        for line in body.split("\n"):
            match = CAST_ENTRY_RE.match(line)
            if match and line.strip() != CAST_HEADER:
                name, payload = match.group(1).strip(), match.group(2)
                if _is_from_source(payload, source_index):
                    recovered = _recover_line(name, payload)
                    if recovered.startswith("["):
                        report["replies_recovered"] += 1
                    else:
                        report["narration_recovered"] += 1
                    body_lines.append(recovered)
                    continue
            body_lines.append(line)

        chunk = "\n".join(recovered_from_block + body_lines).strip("\n")
        if chunk.strip():
            bodies.append(chunk)

    report["cast_entries_merged"] = len(cast_by_key)

    if not cast_by_key and not bodies:
        return "", report

    chunks: list[str] = []
    if cast_by_key:
        chunks.append("\n".join(
            [CAST_HEADER] + [f"[{name}] :: {desc}" for name, desc in cast_by_key.values()]
        ))
    chunks.extend(bodies)
    return "\n\n".join(chunks).strip(), report
