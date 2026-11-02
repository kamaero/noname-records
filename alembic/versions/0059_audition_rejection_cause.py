"""Добавить AuditionRejection.cause — повод письма (approval/scores/manual)

Revision ID: 0056_audition_rejection_cause
Revises: 0055_book_lifecycle
Create Date: 2026-11-02
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0059_audition_rejection_cause"
down_revision: Union[str, None] = "0058_book_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "audition_rejections" not in tables:
        return
    columns = {c["name"] for c in inspector.get_columns("audition_rejections")}
    if "cause" not in columns:
        op.add_column(
            "audition_rejections",
            sa.Column("cause", sa.String(length=12), nullable=False, server_default=""),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "audition_rejections" not in tables:
        return
    columns = {c["name"] for c in inspector.get_columns("audition_rejections")}
    if "cause" in columns:
        op.drop_column("audition_rejections", "cause")
