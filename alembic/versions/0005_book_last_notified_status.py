"""book.last_notified_status (telegram notification dedup)

Revision ID: 0005_book_last_notified_status
Revises: 0004_additive_via_alembic
Create Date: 2026-05-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_book_last_notified_status"
down_revision: Union[str, None] = "0004_additive_via_alembic"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("script_books", "last_notified_status"):
        op.add_column("script_books", sa.Column("last_notified_status", sa.String(40), nullable=False, server_default=""))


def downgrade() -> None:
    if _has_column("script_books", "last_notified_status"):
        op.drop_column("script_books", "last_notified_status")
