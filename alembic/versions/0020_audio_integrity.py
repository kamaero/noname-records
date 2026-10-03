"""целостность аудио: сумма при приёме, место хранения, вердикт сверки

Файл льётся на NAS потоком, и до сих пор ничто не проверяло, что он доехал целым:
обрыв на середине давал запись «загружено» в базе и обрезок на диске, который
проявился бы тишиной на сборке главы — месяцы спустя.

Аддитивно и с проверкой. Пусто у старых записей — честное «не считали».

Revision ID: 0020_audio_integrity
Revises: 0019_chapter_delivered
Create Date: 2026-09-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020_audio_integrity"
down_revision: Union[str, None] = "0019_chapter_delivered"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "audio_files" not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns("audio_files")}


def upgrade() -> None:
    have = _columns()
    if not have:
        return
    if "md5" not in have:
        op.add_column("audio_files", sa.Column("md5", sa.String(32), nullable=False, server_default=""))
    if "location" not in have:
        op.add_column("audio_files", sa.Column("location", sa.String(8), nullable=False, server_default="nas"))
    if "verified_at" not in have:
        op.add_column("audio_files", sa.Column("verified_at", sa.DateTime(), nullable=True))
    if "verify_state" not in have:
        op.add_column("audio_files", sa.Column("verify_state", sa.String(16), nullable=False, server_default=""))


def downgrade() -> None:
    have = _columns()
    for name in ("verify_state", "verified_at", "location", "md5"):
        if name in have:
            op.drop_column("audio_files", name)
