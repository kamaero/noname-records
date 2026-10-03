"""бот: учёт контактов (кто вышел на связь) и время последней сводки владельцу

Revision ID: 0043_bot_contacts
Revises: 0042_bot_broadcasts
Create Date: 2026-10-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0043_bot_contacts"
down_revision: Union[str, None] = "0042_bot_broadcasts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "bot_contacts" not in inspector.get_table_names():
        op.create_table(
            "bot_contacts",
            sa.Column("telegram_user_id", sa.String(32), primary_key=True),
            sa.Column("tg_name", sa.String(200), nullable=False, server_default=""),
            sa.Column("username", sa.String(64), nullable=False, server_default=""),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("reported_at", sa.DateTime(), nullable=True),
        )
    if "studio_settings" in inspector.get_table_names() and \
            "contacts_reported_at" not in {c["name"] for c in inspector.get_columns("studio_settings")}:
        op.add_column("studio_settings", sa.Column("contacts_reported_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if "bot_contacts" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("bot_contacts")
