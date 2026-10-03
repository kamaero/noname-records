"""studio-wide defaults: the rate a role inherits when it has none of its own

Additive and guarded like 0008/0009: created when missing, left alone when present.
The single row is written on first read rather than here, so a database that never
opens the cast screen never grows one.

Revision ID: 0010_studio_settings
Revises: 0009_v2_runs
Create Date: 2026-09-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_studio_settings"
down_revision: Union[str, None] = "0009_v2_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "studio_settings" in _tables():
        return
    op.create_table(
        "studio_settings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("default_rate_rub_per_min", sa.Integer, nullable=False, server_default="1000"),
        sa.Column("updated_by", sa.String(120), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    if "studio_settings" in _tables():
        op.drop_table("studio_settings")
