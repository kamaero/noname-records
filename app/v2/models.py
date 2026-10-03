"""Tables for pipeline v2, kept apart from the ones the old pipeline owns.

The shape follows from one decision: the text is never rewritten. A segment holds the
author's prose as extracted and never changes; attributions and stress marks point
into it by offset, and a correction is a new row with a higher version rather than an
edit. That is what makes fixing one stress mark cost one row instead of a whole
chapter's worth of model calls.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.time_utils import utcnow_naive


class V2Segment(Base):
    __tablename__ = "v2_segments"
    __table_args__ = (
        Index("ix_v2_segments_chapter", "chapter_id", "ordinal", unique=True),
        Index("ix_v2_segments_book", "book_id"),
    )

    # f"{chapter_id}:{ordinal:05d}" — what an annotation points at.
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    chapter_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="paragraph")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class V2Attribution(Base):
    __tablename__ = "v2_attributions"
    __table_args__ = (Index("ix_v2_attributions_segment", "segment_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    segment_id: Mapped[str] = mapped_column(String(80), nullable=False)
    # Offsets inside the segment's own text; a whole paragraph is 0..len.
    span_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    speaker: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # llm | llm_review | operator | import
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="llm")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class V2StressMark(Base):
    __tablename__ = "v2_stress_marks"
    __table_args__ = (Index("ix_v2_stress_segment", "segment_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    segment_id: Mapped[str] = mapped_column(String(80), nullable=False)
    word_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    word_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Where the stressed vowel sits inside the word, so the prose stays clean and the
    # mark can be shown, hidden or corrected without touching the text.
    vowel_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # dict | author | homograph_llm | operator
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="dict")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class V2Run(Base):
    """One pass of the v2 book pipeline: which step it is on and how far it got.

    Progress the UI can read without asking the worker. A heartbeat is a write to
    `updated_at` after every chapter, so a run whose heartbeat stopped is a run whose
    process died.
    """

    __tablename__ = "v2_runs"
    __table_args__ = (
        Index("ix_v2_runs_book", "book_id"),
        Index("ix_v2_runs_book_status", "book_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # queued | running | done | failed | stopped
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    # segment | cast | attribute | stress — the step being worked on, or the last one.
    step: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    chapters_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chapters_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class V2StressSkip(Base):
    """A word the author has waved off: «ударение тут не нужно, его и так все знают».

    Not a stress decision — a stress term must name a stressed vowel, and these words
    have nothing to say about one. It is a statement about the queue: stop asking.
    Per book, because a word ordinary in one author's world may be invented in another's.
    """

    __tablename__ = "v2_stress_skips"
    __table_args__ = (Index("ix_v2_stress_skips_book_word", "book_id", "word", unique=True),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    book_id: Mapped[str] = mapped_column(String(36), nullable=False)
    word: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_uid: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive, nullable=False)
