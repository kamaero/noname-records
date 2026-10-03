"""pipeline v2: immutable segments and the annotations that point at them

Additive only. Nothing existing is touched, and the old pipeline keeps running on
its own tables until v2 has been shown to be better.

The shape follows from one decision: the text is never rewritten. A segment holds
the author's prose as it was extracted and never changes; an attribution or a stress
mark points into it by offset. A correction is a new row with a higher `version`,
never an edit — so the history of who decided what stays readable, and fixing one
stress mark cannot cost a whole chapter's worth of model calls the way it does today.

Revision ID: 0008_v2_segments
Revises: 0007_char_map_gate
Create Date: 2026-09-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_v2_segments"
down_revision: Union[str, None] = "0007_char_map_gate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _tables()

    if "v2_segments" not in existing:
        op.create_table(
            "v2_segments",
            # f"{chapter_id}:{ordinal:05d}" — the id an annotation points at.
            sa.Column("id", sa.String(80), primary_key=True),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("chapter_id", sa.String(36), nullable=False),
            sa.Column("ordinal", sa.Integer, nullable=False),
            sa.Column("kind", sa.String(16), nullable=False, server_default="paragraph"),
            # The author's prose, unchanged and unmarked. Stress lives in v2_stress_marks.
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("char_start", sa.Integer, nullable=False, server_default="0"),
            sa.Column("char_end", sa.Integer, nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )
        op.create_index("ix_v2_segments_chapter", "v2_segments", ["chapter_id", "ordinal"], unique=True)
        op.create_index("ix_v2_segments_book", "v2_segments", ["book_id"])

    if "v2_attributions" not in existing:
        op.create_table(
            "v2_attributions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("segment_id", sa.String(80), nullable=False),
            # Offsets within the segment's own text; a whole paragraph is 0..len.
            sa.Column("span_start", sa.Integer, nullable=False, server_default="0"),
            sa.Column("span_end", sa.Integer, nullable=False, server_default="0"),
            sa.Column("speaker", sa.String(200), nullable=False, server_default=""),
            sa.Column("confidence", sa.Float, nullable=False, server_default="0"),
            # llm | llm_review | operator | import
            sa.Column("source", sa.String(20), nullable=False, server_default="llm"),
            # Rises on rewrite; older rows are kept so a decision can be traced back.
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )
        op.create_index("ix_v2_attributions_segment", "v2_attributions", ["segment_id", "version"])

    if "v2_stress_marks" not in existing:
        op.create_table(
            "v2_stress_marks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("segment_id", sa.String(80), nullable=False),
            sa.Column("word_start", sa.Integer, nullable=False, server_default="0"),
            sa.Column("word_end", sa.Integer, nullable=False, server_default="0"),
            # Where the stressed vowel sits inside the word, so the text stays clean
            # and the mark can be shown, hidden or corrected without touching prose.
            sa.Column("vowel_offset", sa.Integer, nullable=False, server_default="0"),
            # dict | author | homograph_llm | operator
            sa.Column("source", sa.String(20), nullable=False, server_default="dict"),
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime, nullable=True),
        )
        op.create_index("ix_v2_stress_segment", "v2_stress_marks", ["segment_id", "version"])


def downgrade() -> None:
    for table in ("v2_stress_marks", "v2_attributions", "v2_segments"):
        if table in _tables():
            op.drop_table(table)
