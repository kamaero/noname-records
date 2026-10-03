"""бот: рассылка касту книги или всем дикторам

Аддитивно и с проверкой наличия: миграции применяются сами при старте службы.

Revision ID: 0042_bot_broadcasts
Revises: 0041_role_deadlines
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0042_bot_broadcasts"
down_revision: Union[str, None] = "0041_role_deadlines"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "bot_broadcasts" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "bot_broadcasts",
        sa.Column("id", sa.String(8), primary_key=True),
        sa.Column("admin_tid", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(8), nullable=False, server_default=""),
        sa.Column("book_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("include_proposed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.String(10), nullable=False, server_default="choose"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    if "bot_broadcasts" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("bot_broadcasts")
