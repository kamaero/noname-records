"""The author's own text for one chapter, cut to line up with the script.

The reader shows annotated segments; the split view shows what the author wrote
next to them. Those are two different things on purpose: the segments are what
the pipeline works on, `ScriptChapter.source_text` is what came in. Slicing the
original at the segments' own offsets keeps both panes in step and, as a side
effect, makes anything the segments do not cover visible instead of silently
absent — that prose comes back as a `gap` block.

Nothing here reads annotations: attribution and stress belong to the right pane,
which already has them from `reader.build_chapter_payload`.
"""
from __future__ import annotations

import re

from app.models import ScriptChapter
from app.v2.store import load_chapter_segments

_BLANK_LINE = re.compile(r"\n{2,}")


def _pieces(text: str) -> list[str]:
    """Non-blank blocks of `text`, in order — the same cut the segmenter makes."""
    return [piece.strip() for piece in _BLANK_LINE.split(text or "") if piece.strip()]


def _gap_blocks(text: str) -> list[dict]:
    return [{"segment_id": "", "ordinal": -1, "kind": "gap", "text": piece} for piece in _pieces(text)]


def _fits(source: str, segments) -> bool:
    """True when every segment still sits at its recorded offsets in `source`."""
    if not source or not segments:
        return False
    return all(
        0 <= int(segment.char_start or 0) <= int(segment.char_end or 0) <= len(source)
        and source[int(segment.char_start or 0) : int(segment.char_end or 0)] == (segment.text or "")
        for segment in segments
    )


def build_source_view(db, chapter_id: str) -> dict | None:
    """Blocks of the chapter's original text, keyed to the segments; None if unknown.

    `aligned` says whether the blocks were really cut from the original. When the
    source text was replaced after segmentation (a re-upload, a legacy import that
    carried no source), the segments' own text is shown instead — still readable,
    but no longer proof that nothing was lost, and the pane says so.
    """
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return None

    source = str(chapter.source_text or "")
    segments = load_chapter_segments(db, chapter_id=chapter.id)
    blocks: list[dict] = []
    aligned = True

    if not segments:
        blocks = _gap_blocks(source)
    elif not _fits(source, segments):
        aligned = False
        blocks = [
            {"segment_id": segment.id, "ordinal": int(segment.ordinal or 0),
             "kind": str(segment.kind or "paragraph"), "text": str(segment.text or "")}
            for segment in segments
        ]
    else:
        cursor = 0
        for segment in segments:
            start, end = int(segment.char_start or 0), int(segment.char_end or 0)
            if start > cursor:
                blocks.extend(_gap_blocks(source[cursor:start]))
            blocks.append({
                "segment_id": segment.id,
                "ordinal": int(segment.ordinal or 0),
                "kind": str(segment.kind or "paragraph"),
                "text": source[start:end],
            })
            cursor = max(cursor, end)
        blocks.extend(_gap_blocks(source[cursor:]))

    gaps = [block for block in blocks if block["kind"] == "gap"]
    return {
        "ok": True,
        "chapter": {
            "id": chapter.id,
            "index": int(chapter.chapter_index or 0),
            "title": str(chapter.chapter_title or "").strip(),
        },
        "aligned": aligned,
        "blocks": blocks,
        "counts": {
            "blocks": len(blocks),
            "segments": len(segments),
            "gaps": len(gaps),
            "gap_chars": sum(len(block["text"]) for block in gaps),
        },
    }
