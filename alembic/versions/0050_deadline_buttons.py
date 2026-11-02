"""сроки: отметки кнопок бота — взята, отказ, запрос продления (пакет B)

Revision ID: 0047_deadline_buttons
Revises: 0046_score_reason
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0050_deadline_buttons"
down_revision: Union[str, None] = "0049_score_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW = (
    ("accepted_at", sa.DateTime()),
    ("declined_at", sa.DateTime()),
    ("extend_request_days", sa.Integer()),
    ("extend_request_at", sa.DateTime()),
)


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "role_deadlines" not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns("role_deadlines")}


def upgrade() -> None:
    columns = _columns()
    if not columns:
        return
    for name, type_ in NEW:
        if name not in columns:
            op.add_column("role_deadlines", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    present = [name for name, _ in NEW if name in _columns()]
    if present:
        with op.batch_alter_table("role_deadlines") as batch:
            for name in present:
                batch.drop_column(name)
