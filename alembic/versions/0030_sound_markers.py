"""звуковая разметка: места, пары мест, маркеры

Отдельный слой для звукорежиссёра; сценарий не трогается.

Revision ID: 0030_sound_markers
Revises: 0029_chapter_session_fingerprint
Create Date: 2026-09-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0030_sound_markers"
down_revision: Union[str, None] = "0029_chapter_session_fingerprint"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("sound_places"):
        op.create_table(
            "sound_places",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("name", sa.String(200), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("ambience_queries", sa.JSON(), nullable=False),
            sa.Column("merged_into", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_sound_places_book", "sound_places", ["book_id"])
    if not _has_table("sound_place_pairs"):
        op.create_table(
            "sound_place_pairs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("place_a", sa.String(36), nullable=False),
            sa.Column("place_b", sa.String(36), nullable=False),
            sa.Column("status", sa.String(12), nullable=False, server_default="candidate"),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_sound_place_pairs_book", "sound_place_pairs", ["book_id", "status"])
    if not _has_table("sound_markers"):
        op.create_table(
            "sound_markers",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("chapter_id", sa.String(36), nullable=False),
            sa.Column("segment_id", sa.String(80), nullable=False, server_default=""),
            sa.Column("kind", sa.String(12), nullable=False),
            sa.Column("status", sa.String(12), nullable=False, server_default="active"),
            sa.Column("source", sa.String(8), nullable=False, server_default="llm"),
            sa.Column("place_id", sa.String(36), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("quote", sa.Text(), nullable=False, server_default=""),
            sa.Column("text_sha256", sa.String(64), nullable=False, server_default=""),
            sa.Column("run_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_sound_markers_chapter", "sound_markers", ["chapter_id", "status"])
        op.create_index("ix_sound_markers_book", "sound_markers", ["book_id"])


def downgrade() -> None:
    for table in ("sound_markers", "sound_place_pairs", "sound_places"):
        if _has_table(table):
            op.drop_table(table)
