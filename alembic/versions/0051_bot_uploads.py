"""бот: файлы проб ждут выбора роли (пакет C2)

Revision ID: 0048_bot_uploads
Revises: 0047_deadline_buttons
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0051_bot_uploads"
down_revision: Union[str, None] = "0050_deadline_buttons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "bot_uploads" in _tables():
        return
    op.create_table(
        "bot_uploads",
        sa.Column("id", sa.String(12), primary_key=True),
        sa.Column("telegram_user_id", sa.String(32), nullable=False),
        sa.Column("file_id", sa.String(255), nullable=False),
        sa.Column("file_unique_id", sa.String(64), nullable=False, server_default=""),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("mime_type", sa.String(80), nullable=False, server_default=""),
        sa.Column("file_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("character_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("actor_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("state", sa.String(10), nullable=False, server_default="choose"),
        sa.Column("audio_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bot_uploads_user", "bot_uploads", ["telegram_user_id"])


def downgrade() -> None:
    if "bot_uploads" in _tables():
        op.drop_index("ix_bot_uploads_user", table_name="bot_uploads")
        op.drop_table("bot_uploads")
