"""оценка пробы: причина низкой оценки автора (голос, манера, всё сразу)

Revision ID: 0046_score_reason
Revises: 0045_rejection_cause
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0049_score_reason"
down_revision: Union[str, None] = "0048_rejection_cause"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "audition_scores" not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns("audition_scores")}


def upgrade() -> None:
    columns = _columns()
    if columns and "reason" not in columns:
        op.add_column("audition_scores", sa.Column("reason", sa.String(length=10), nullable=False, server_default=""))


def downgrade() -> None:
    if "reason" in _columns():
        with op.batch_alter_table("audition_scores") as batch:
            batch.drop_column("reason")
