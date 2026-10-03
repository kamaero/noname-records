"""per-book switch: an approved chapter opens for recording at once

Additive and guarded like the revisions before it.

Revision ID: 0013_auto_publish
Revises: 0012_usd_rate
Create Date: 2026-09-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_auto_publish"
down_revision: Union[str, None] = "0012_usd_rate"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {row["name"] for row in sa.inspect(op.get_bind()).get_columns("script_books")}


def upgrade() -> None:
    if "auto_publish" not in _columns():
        op.add_column("script_books", sa.Column("auto_publish", sa.String(5), nullable=False, server_default="false"))


def downgrade() -> None:
    if "auto_publish" in _columns():
        op.drop_column("script_books", "auto_publish")
