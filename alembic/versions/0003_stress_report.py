"""stress report column

Revision ID: 0003_stress_report
Revises: 0002_author_profile
Create Date: 2026-05-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_stress_report"
down_revision: Union[str, None] = "0002_author_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("script_chapters", "stress_report_json"):
        op.add_column("script_chapters", sa.Column("stress_report_json", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    if _has_column("script_chapters", "stress_report_json"):
        op.drop_column("script_chapters", "stress_report_json")
