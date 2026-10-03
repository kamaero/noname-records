"""char-map gate: script_books.char_map_version + characters.operator_note

Revision ID: 0007_char_map_gate
Revises: 0006_audio_line_index
Create Date: 2026-06-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_char_map_gate"
down_revision: Union[str, None] = "0006_audio_line_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("script_books", "char_map_version"):
        op.add_column("script_books", sa.Column("char_map_version", sa.Integer(), nullable=False, server_default="0"))
    if not _has_column("characters", "operator_note"):
        op.add_column("characters", sa.Column("operator_note", sa.String(), nullable=False, server_default=""))


def downgrade() -> None:
    if _has_column("characters", "operator_note"):
        op.drop_column("characters", "operator_note")
    if _has_column("script_books", "char_map_version"):
        op.drop_column("script_books", "char_map_version")
