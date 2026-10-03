"""What both runners do to one chapter, so the measurement and the book agree.

`attribute_ground_truth.py` runs on the marked-up байки and `attribute_book.py` on
«Крылья»; if each wired the two passes together on its own, the benchmark would be
scoring a slightly different procedure than the one the book gets. The wiring lives
here once, and each script only decides where units and the cast come from and where
the records go.
"""
from __future__ import annotations

from app.v2.attribute import attribute_units, new_stats, review_low_confidence


def merge_stats(into: dict, more: dict) -> dict:
    for key in ("calls", "prompt_tokens", "completion_tokens"):
        into[key] = int(into.get(key) or 0) + int(more.get(key) or 0)
    return into


def attribute_chapter(units, *, cast, cast_lines, llm, review: bool = True, on_batch=None):
    """`(records, problems, stats)` — the main pass, then the second look at what came back weak."""
    records, problems, stats = attribute_units(
        units, cast=cast, cast_lines=cast_lines, llm=llm, on_batch=on_batch,
    )
    if review:
        records, more_problems, more_stats = review_low_confidence(
            records, units, cast=cast, cast_lines=cast_lines, llm=llm,
        )
        problems = problems + more_problems
        stats = merge_stats(dict(stats), more_stats)
    return records, problems, stats


def parse_chapter_range(spec: str | None) -> set[int] | None:
    """«1-3,7» into {1, 2, 3, 7}; empty means every chapter."""
    if not spec or not spec.strip():
        return None
    chosen: set[int] = set()
    for piece in spec.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "-" in piece:
            low, high = piece.split("-", 1)
            chosen.update(range(int(low), int(high) + 1))
        else:
            chosen.add(int(piece))
    return chosen


def bench_record(record: dict) -> dict:
    """Exactly the keys the benchmark reads, in the order a person reads a JSONL line."""
    return {
        "chapter": record["chapter"],
        "ordinal": record["ordinal"],
        "text": record["text"],
        "span_start": record["span_start"],
        "span_end": record["span_end"],
        "speaker": record["speaker"],
        "confidence": record.get("confidence", 0.0),
        "source": record.get("source", "llm"),
    }


__all__ = ["attribute_chapter", "bench_record", "merge_stats", "new_stats", "parse_chapter_range"]
