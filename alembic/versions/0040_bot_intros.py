"""бот: знакомство — кого бот спросил «Как вас зовут?»

Аддитивно и с проверкой наличия: миграции применяются сами при старте службы.

Revision ID: 0040_bot_intros
Revises: 0039_dictors
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0040_bot_intros"
down_revision: Union[str, None] = "0039_dictors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("bot_intros"):
        op.create_table(
            "bot_intros",
            sa.Column("telegram_user_id", sa.String(32), primary_key=True),
            sa.Column("asked_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    if _has_table("bot_intros"):
        op.drop_table("bot_intros")
