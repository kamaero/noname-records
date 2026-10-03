"""отпечаток архивной сессии главы и пометка «устарела»

Сессия в архиве устаревает, когда правка разметки или дозапись меняют сверку. Фон
пересобирает такую главу сам, а по отпечатку отличает настоящую перемену от пустой.

Аддитивно и с проверкой наличия графы: миграции применяются сами при старте службы.

Revision ID: 0029_chapter_session_fingerprint
Revises: 0028_consilium_decided_speaker
Create Date: 2026-09-15
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0029_chapter_session_fingerprint"
down_revision: Union[str, None] = "0028_consilium_decided_speaker"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("script_chapters", "session_sha256"):
        op.add_column("script_chapters", sa.Column("session_sha256", sa.String(64), nullable=False, server_default=""))
    if not _has_column("script_chapters", "session_outdated_at"):
        op.add_column("script_chapters", sa.Column("session_outdated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    for column in ("session_outdated_at", "session_sha256"):
        if _has_column("script_chapters", column):
            op.drop_column("script_chapters", column)
