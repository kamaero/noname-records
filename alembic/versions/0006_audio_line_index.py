"""audio_files.line_index (recording: NULL=role take, N=per-replica patch)

Revision ID: 0006_audio_line_index
Revises: 0005_book_last_notified_status
Create Date: 2026-06-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_audio_line_index"
down_revision: Union[str, None] = "0005_book_last_notified_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("audio_files", "line_index"):
        op.add_column("audio_files", sa.Column("line_index", sa.Integer(), nullable=True))


def downgrade() -> None:
    if _has_column("audio_files", "line_index"):
        op.drop_column("audio_files", "line_index")
