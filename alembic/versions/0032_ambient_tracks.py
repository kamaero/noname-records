"""эмбиент: таблица треков ElevenLabs

Один трек на активную сцену главы (`ambient_tracks.marker_id` — строка `sound_markers`
с `kind='scene'`). Файл на диске — обычная `AudioFile` с `kind='ambient'` (миграция 0032
не трогает `audio_files`, поле `kind` там уже есть).

Revision ID: 0032_ambient_tracks
Revises: 0031_session_markers_skipped
Create Date: 2026-09-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0032_ambient_tracks"
down_revision: Union[str, None] = "0031_session_markers_skipped"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("ambient_tracks"):
        op.create_table(
            "ambient_tracks",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("chapter_id", sa.String(36), nullable=False),
            sa.Column("marker_id", sa.String(36), nullable=False),
            sa.Column("audio_file_id", sa.String(36), nullable=True),
            sa.Column("prompt", sa.Text(), nullable=False, server_default=""),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("song_id", sa.String(120), nullable=False, server_default=""),
            sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_ambient_tracks_chapter_status", "ambient_tracks", ["chapter_id", "status"])
        op.create_index("ix_ambient_tracks_marker", "ambient_tracks", ["marker_id"])


def downgrade() -> None:
    if _has_table("ambient_tracks"):
        op.drop_table("ambient_tracks")
