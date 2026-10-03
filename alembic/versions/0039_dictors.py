"""раздел «Дикторы»: карточки, демо, ссылки, агрегат назначений, журнал рекастов

Аддитивно и с проверкой наличия: миграции применяются сами при старте службы.

Revision ID: 0039_dictors
Revises: 0038_drop_v1_tables
Create Date: 2026-09-30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039_dictors"
down_revision: Union[str, None] = "0038_drop_v1_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("dictor_profiles"):
        op.create_table(
            "dictor_profiles",
            sa.Column("user_id", sa.String(36), primary_key=True),
            sa.Column("telegram_username", sa.String(64), nullable=False, server_default=""),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("main_demo_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if not _has_table("dictor_demos"):
        op.create_table(
            "dictor_demos",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("title", sa.String(255), nullable=False, server_default=""),
            sa.Column("stored_key", sa.String(512), nullable=False, server_default=""),
            sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("md5", sa.String(32), nullable=False, server_default=""),
            sa.Column("trimmed", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source", sa.String(20), nullable=False, server_default="upload"),
            sa.Column("source_url", sa.String(1024), nullable=False, server_default=""),
            sa.Column("source_ref", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_dictor_demos_user", "dictor_demos", ["user_id"])
    if not _has_table("dictor_links"):
        op.create_table(
            "dictor_links",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("url", sa.String(1024), nullable=False),
            sa.Column("title", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_dictor_links_user", "dictor_links", ["user_id"])
    if not _has_table("dictor_assignments"):
        op.create_table(
            "dictor_assignments",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), nullable=False),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("character_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("role_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("state", sa.String(20), nullable=False, server_default="approved"),
            sa.Column("recorded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_dictor_assignments_user", "dictor_assignments", ["user_id"])
        op.create_index("ix_dictor_assignments_book", "dictor_assignments", ["book_id"])
    if not _has_table("recasts"):
        op.create_table(
            "recasts",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("author_character_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("role_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("from_actor", sa.String(120), nullable=False, server_default=""),
            sa.Column("to_actor", sa.String(120), nullable=False, server_default=""),
            sa.Column("reason", sa.String(20), nullable=False, server_default="other"),
            sa.Column("comment", sa.Text(), nullable=False, server_default=""),
            sa.Column("books_changed", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("books_kept", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("actor_user_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    for name in ("recasts", "dictor_assignments", "dictor_links", "dictor_demos", "dictor_profiles"):
        if _has_table(name):
            op.drop_table(name)
