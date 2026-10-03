"""pipeline v2: the run table the progress screen reads

Additive only, guarded like 0008: the table is created when it is missing and left
alone when it is there, so the revision can be applied to a database that already
got the table from a scratch script.

Revision ID: 0009_v2_runs
Revises: 0008_v2_segments
Create Date: 2026-09-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_v2_runs"
down_revision: Union[str, None] = "0008_v2_segments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "v2_runs" in _tables():
        return
    op.create_table(
        "v2_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("book_id", sa.String(36), nullable=False),
        # queued | running | done | failed | stopped
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        # segment | cast | attribute | stress
        sa.Column("step", sa.String(20), nullable=False, server_default=""),
        sa.Column("chapters_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chapters_done", sa.Integer, nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer, nullable=False, server_default="0"),
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error", sa.Text, nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime, nullable=True),
        # Written after every chapter — the heartbeat.
        sa.Column("updated_at", sa.DateTime, nullable=False),
        sa.Column("finished_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_v2_runs_book", "v2_runs", ["book_id"])
    op.create_index("ix_v2_runs_book_status", "v2_runs", ["book_id", "status"])


def downgrade() -> None:
    if "v2_runs" in _tables():
        op.drop_table("v2_runs")
