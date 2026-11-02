"""отказ по пробе: повод письма (утверждение другого или оценки ниже 7)

Без повода `_settle` не отличал отказ, заведённый утверждением соперника, от отказа по
оценкам: снятое утверждение при двух старых оценках ниже 7 отправляло письмо, хотя
оценок никто не нажимал. Существующие строки заведены утверждением — `approval`.

Revision ID: 0045_rejection_cause
Revises: 0044_audition_scores
Create Date: 2026-10-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0048_rejection_cause"
down_revision: Union[str, None] = "0047_audition_scores"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "audition_rejections" in inspector.get_table_names() and \
            "cause" not in {c["name"] for c in inspector.get_columns("audition_rejections")}:
        op.add_column("audition_rejections",
                      sa.Column("cause", sa.String(length=10), nullable=False, server_default="approval"))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "audition_rejections" in inspector.get_table_names() and \
            "cause" in {c["name"] for c in inspector.get_columns("audition_rejections")}:
        with op.batch_alter_table("audition_rejections") as batch:
            batch.drop_column("cause")
