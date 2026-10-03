"""сроки проб и ролей: таблица role_deadlines, дата срока ролей в настройках студии

Аддитивно и с проверкой наличия: миграции применяются сами при старте службы.

Нынешние «Имя?» в касте получают закрытую строку `baseline`: иначе первая же сверка
открыла бы им 48 часов, и через сутки давно позванным на пробу ушли бы напоминания.

Revision ID: 0041_role_deadlines
Revises: 0040_bot_intros
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_role_deadlines"
down_revision: Union[str, None] = "0040_bot_intros"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_table("role_deadlines"):
        op.create_table(
            "role_deadlines",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("character_id", sa.String(36), nullable=False),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("actor_name", sa.String(120), nullable=False),
            sa.Column("kind", sa.String(10), nullable=False),
            sa.Column("due_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("close_reason", sa.String(12), nullable=True),
            sa.Column("reminded_before_at", sa.DateTime(), nullable=True),
            sa.Column("reminded_overdue_at", sa.DateTime(), nullable=True),
            sa.Column("recast_notified_at", sa.DateTime(), nullable=True),
            sa.Column("extended_by", sa.String(120), nullable=False, server_default=""),
        )
        op.create_index("ix_role_deadlines_character", "role_deadlines", ["character_id"])
        op.create_index("ix_role_deadlines_open", "role_deadlines", ["closed_at"])
        if _has_table("characters"):
            op.execute(
                "INSERT INTO role_deadlines (id, character_id, book_id, actor_name, kind, due_at, created_at, "
                "closed_at, close_reason, extended_by) "
                "SELECT lower(hex(randomblob(16))), id, book_id, trim(rtrim(trim(actor_name), '?')), 'audition', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 'baseline', '' FROM characters "
                "WHERE trim(actor_name) LIKE '%?' AND trim(rtrim(trim(actor_name), '?')) <> ''"
            )
    if _has_table("studio_settings"):
        if not _has_column("studio_settings", "role_deadline_date"):
            op.add_column("studio_settings", sa.Column("role_deadline_date", sa.String(10), nullable=False,
                                                       server_default="2026-12-31"))
        if not _has_column("studio_settings", "deadline_digest_day"):
            op.add_column("studio_settings", sa.Column("deadline_digest_day", sa.String(10), nullable=False,
                                                       server_default=""))


def downgrade() -> None:
    if _has_table("role_deadlines"):
        op.drop_table("role_deadlines")
