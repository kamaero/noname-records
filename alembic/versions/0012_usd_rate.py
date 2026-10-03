"""studio-wide dollar rate, for providers that bill in dollars

Additive and guarded like the revisions before it.

Revision ID: 0012_usd_rate
Revises: 0011_book_model_choice
Create Date: 2026-09-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_usd_rate"
down_revision: Union[str, None] = "0011_book_model_choice"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "studio_settings" not in inspector.get_table_names():
        return set()
    return {row["name"] for row in inspector.get_columns("studio_settings")}


def upgrade() -> None:
    columns = _columns()
    if columns and "usd_rub_rate" not in columns:
        op.add_column("studio_settings", sa.Column("usd_rub_rate", sa.Float, nullable=False, server_default="0"))


def downgrade() -> None:
    if "usd_rub_rate" in _columns():
        op.drop_column("studio_settings", "usd_rub_rate")
