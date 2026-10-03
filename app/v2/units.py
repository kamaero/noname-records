"""One shape of "numbered piece of text", fed from two places.

Attribution is measured on «Полумракские байки», because that is where the owner's
hand-marked reference is, and it runs on «Крылья полумрака», because that is the book
being made. The байки are not a book in the system and will not become one — they are
finished and voiced — so they arrive as docx paragraphs, while «Крылья» arrives as
rows of v2_segments.

If those fed two code paths, the measurement would be of something other than the
thing that runs. So both become Units first and the attribution knows nothing else.

The two sources number differently, on purpose: the docx reader skips the heading and
the cast legend and counts from the first paragraph of prose, while the segmenter
counts the heading as segment zero. What has to agree is the shape, not the numbering
— and a Unit carries enough of its origin that a span comes back to the right place.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Unit:
    """A piece of text the model is asked about, and where its answer belongs."""

    id: str
    chapter: str
    ordinal: int
    text: str

    def as_record(self, *, span_start: int, span_end: int, speaker: str, confidence: float = 0.0) -> dict:
        """One attributed span in the shape the benchmark and the store both read."""
        return {
            "chapter": self.chapter,
            "ordinal": self.ordinal,
            "unit_id": self.id,
            "text": self.text,
            "span_start": span_start,
            "span_end": span_end,
            "speaker": speaker,
            "confidence": confidence,
        }


def units_from_segments(segments) -> list[Unit]:
    """From v2_segments rows or Segment objects — the path «Крылья» takes."""
    return [
        Unit(id=s.id, chapter=s.chapter_id, ordinal=int(s.ordinal), text=s.text)
        for s in segments
    ]


def units_from_chapter(chapter) -> list[Unit]:
    """From a read marked-up docx — the path the measurement takes."""
    title = getattr(chapter, "title", "") or getattr(chapter, "path", "")
    return [
        Unit(id=f"{title}:{p.ordinal:05d}", chapter=title, ordinal=int(p.ordinal), text=p.text)
        for p in getattr(chapter, "paragraphs", [])
    ]
