"""реакции автора на пробы и вежливый отказ диктору

Аддитивно и с проверкой наличия таблицы: миграции применяются сами при старте службы.

Revision ID: 0037_audition_reactions
Revises: 0036_book_illustrations
Create Date: 2026-09-25
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0037_audition_reactions"
down_revision: Union[str, None] = "0036_book_illustrations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("audition_reactions"):
        op.create_table(
            "audition_reactions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("audio_file_id", sa.String(length=36), nullable=False),
            sa.Column("voter_uid", sa.String(length=36), nullable=False),
            sa.Column("voter_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("value", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("audio_file_id", "voter_uid", name="uq_audition_reactions_audio_voter"),
        )
        op.create_index("ix_audition_reactions_audio", "audition_reactions", ["audio_file_id"])
    if not _has_table("audition_rejections"):
        op.create_table(
            "audition_rejections",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("book_id", sa.String(length=36), nullable=False),
            sa.Column("book_code", sa.String(length=40), nullable=False, server_default=""),
            sa.Column("role", sa.String(length=120), nullable=False),
            sa.Column("actor_name", sa.String(length=120), nullable=False),
            sa.Column("due_at", sa.DateTime(), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=False, server_default="pending"),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("book_id", "role", "actor_name", name="uq_audition_rejections_pair"),
        )
        op.create_index("ix_audition_rejections_due", "audition_rejections", ["status", "due_at"])


def downgrade() -> None:
    for name in ("audition_rejections", "audition_reactions"):
        if _has_table(name):
            op.drop_table(name)
