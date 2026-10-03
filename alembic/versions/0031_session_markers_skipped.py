"""счётчик маркеров без места в архивной сессии главы

Сколько маркеров звукорежиссёра не нашли себе места в последней собранной сессии
(их абзац ещё не записан) — считает и кладёт сюда `archive_chapter_session`, а
таблица ASR только показывает.

Аддитивно и с проверкой наличия графы: миграции применяются сами при старте службы.

Revision ID: 0031_session_markers_skipped
Revises: 0030_sound_markers
Create Date: 2026-09-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0031_session_markers_skipped"
down_revision: Union[str, None] = "0030_sound_markers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("script_chapters", "session_markers_skipped"):
        op.add_column(
            "script_chapters",
            sa.Column("session_markers_skipped", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if _has_column("script_chapters", "session_markers_skipped"):
        op.drop_column("script_chapters", "session_markers_skipped")
