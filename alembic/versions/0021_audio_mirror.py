"""зеркало на NAS: отметка о второй копии и о файле проекта главы

Файл существовал в одном экземпляре: пропадёт том — переписывать нечего,
дикторов второй раз не соберёшь. Отметка нужна, чтобы отличить «копия есть
и проверена» от «ещё не копировали», и чтобы окно одной копии было видно.

Аддитивно и с проверкой. Пусто у старых записей — честное «не копировали».

Revision ID: 0021_audio_mirror
Revises: 0020_audio_integrity
Create Date: 2026-09-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_audio_mirror"
down_revision: Union[str, None] = "0020_audio_integrity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    audio = _columns("audio_files")
    if audio and "mirrored_at" not in audio:
        op.add_column("audio_files", sa.Column("mirrored_at", sa.DateTime(), nullable=True))
    if audio and "mirror_state" not in audio:
        op.add_column("audio_files", sa.Column("mirror_state", sa.String(16), nullable=False, server_default=""))
    chapters = _columns("script_chapters")
    if chapters and "session_archived_at" not in chapters:
        op.add_column("script_chapters", sa.Column("session_archived_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    audio = _columns("audio_files")
    for name in ("mirror_state", "mirrored_at"):
        if name in audio:
            op.drop_column("audio_files", name)
    if "session_archived_at" in _columns("script_chapters"):
        op.drop_column("script_chapters", "session_archived_at")
