"""Cut a book into segments that keep their identity.

The whole of v2 rests on the text never changing: a model annotates it, an annotation
points at a segment by id, and a correction is a new annotation rather than a new
text. That only holds if the ids are stable — the same source must always yield the
same ids, and a chapter appended at the end must not renumber anything before it.
Hence `chapter_id:ordinal` and an ordinal that counts within its own chapter.

A paragraph is a block between blank lines. Measured on «Крылья полумрака» that gives
13 278 blocks, none longer than 1033 characters and none carrying an internal newline,
so the sentence-level cut below never fires for that book — but a book whose author
writes in longer breaths must not hand a model a segment it cannot annotate.

Deliberately imports nothing from `script_pipeline`: the heading rule is small enough
to restate, and v2 is meant to stand on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# The same shape the old detector looks for, restated rather than imported. Anchored to
# the start of a line so «глава города» in the middle of a sentence is not a heading.
_HEADING = re.compile(
    r"^[ \t]*(?:ГЛАВА|Глава|CHAPTER|Chapter)\s+[0-9IVXLCА-Яа-яA-Za-z._-]+.*$",
    re.MULTILINE,
)

_BLOCK_SEPARATOR = re.compile(r"\n{2,}")
# A sentence ends at .!?… followed by space. Kept simple on purpose: this only decides
# where an over-long paragraph may be cut, never what the text says.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")

DEFAULT_MAX_CHARS = 1500


@dataclass(frozen=True)
class Segment:
    id: str
    chapter_id: str
    ordinal: int
    kind: str
    text: str
    char_start: int
    char_end: int


def find_chapter_starts(text: str) -> list[int]:
    """Offsets in `text` where a chapter heading begins."""
    return [match.start() for match in _HEADING.finditer(text or "")]


def _is_heading(block: str) -> bool:
    return bool(_HEADING.match(block.strip()))


def _blocks_with_offsets(text: str) -> list[tuple[int, int]]:
    """(start, end) of every non-blank block, as offsets into `text`."""
    spans: list[tuple[int, int]] = []
    position = 0
    for piece in _BLOCK_SEPARATOR.split(text or ""):
        start = position
        position += len(piece)
        # Put back the separator that split() consumed.
        if position < len(text or ""):
            match = _BLOCK_SEPARATOR.match(text, position)
            position += len(match.group(0)) if match else 0
        stripped = piece.strip()
        if not stripped:
            continue
        offset = piece.index(stripped)
        spans.append((start + offset, start + offset + len(stripped)))
    return spans


def _sentence_spans(text: str, start: int, end: int, max_chars: int) -> list[tuple[int, int]]:
    """Cut one over-long block at sentence ends, never inside a sentence.

    A sentence longer than the limit comes back whole: half a sentence is a fragment
    nobody can attribute, which is worse than a segment that runs long.
    """
    block = text[start:end]
    if len(block) <= max_chars:
        return [(start, end)]

    pieces: list[tuple[int, int]] = []
    cursor = 0
    for part in _SENTENCE_END.split(block):
        if not part:
            continue
        index = block.index(part, cursor)
        pieces.append((index, index + len(part)))
        cursor = index + len(part)

    spans: list[tuple[int, int]] = []
    group_start = group_end = None
    for piece_start, piece_end in pieces:
        if group_start is None:
            group_start, group_end = piece_start, piece_end
            continue
        if piece_end - group_start <= max_chars:
            group_end = piece_end
        else:
            spans.append((group_start, group_end))
            group_start, group_end = piece_start, piece_end
    if group_start is not None:
        spans.append((group_start, group_end))

    return [(start + a, start + b) for a, b in spans] or [(start, end)]


def segment_chapter(text: str, *, chapter_id: str, max_chars: int = DEFAULT_MAX_CHARS) -> list[Segment]:
    """One chapter's text into segments, numbered from zero within the chapter."""
    source = text or ""
    segments: list[Segment] = []
    ordinal = 0
    for start, end in _blocks_with_offsets(source):
        heading = _is_heading(source[start:end])
        pieces = [(start, end)] if heading else _sentence_spans(source, start, end, max_chars)
        for piece_start, piece_end in pieces:
            segments.append(Segment(
                id=f"{chapter_id}:{ordinal:05d}",
                chapter_id=chapter_id,
                ordinal=ordinal,
                kind="heading" if heading else "paragraph",
                text=source[piece_start:piece_end],
                char_start=piece_start,
                char_end=piece_end,
            ))
            ordinal += 1
    return segments
