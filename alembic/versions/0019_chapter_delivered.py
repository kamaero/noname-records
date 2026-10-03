"""глава ушла в сведение — и больше не мельтешит среди тех, что ждут

Скачали архив — система об этом не знала, и глава оставалась в списке ожидающих
наравне с теми, к которым ещё никто не притрагивался.

Аддитивно и с проверкой. Пусто у всех старых глав — это честное «не забирали».

Revision ID: 0019_chapter_delivered
Revises: 0018_audio_probe
Create Date: 2026-09-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_chapter_delivered"
down_revision: Union[str, None] = "0018_audio_probe"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "script_chapters" not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns("script_chapters")}


def upgrade() -> None:
    have = _columns()
    if have and "delivered_at" not in have:
        op.add_column("script_chapters", sa.Column("delivered_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if "delivered_at" in _columns():
        op.drop_column("script_chapters", "delivered_at")
