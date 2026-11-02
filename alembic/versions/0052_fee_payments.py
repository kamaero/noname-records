"""«Смета»: журнал выплат дикторам — авансы, выплаты, зачёты (06.10)

Revision ID: 0049_fee_payments
Revises: 0048_bot_uploads
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052_fee_payments"
down_revision: Union[str, None] = "0051_bot_uploads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "fee_payments" in _tables():
        return
    op.create_table(
        "fee_payments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("book_id", sa.String(36), nullable=False, server_default=""),
        sa.Column("role_key", sa.String(36), nullable=False, server_default=""),
        sa.Column("role_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("actor_name", sa.String(120), nullable=False),
        sa.Column("telegram_user_id", sa.String(32), nullable=False, server_default=""),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount_rub", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
        sa.Column("paid_at", sa.DateTime(), nullable=False),
        sa.Column("paid_by", sa.String(120), nullable=False, server_default=""),
    )
    op.create_index("ix_fee_payments_role", "fee_payments", ["book_id", "role_key", "actor_name"])


def downgrade() -> None:
    if "fee_payments" in _tables():
        op.drop_index("ix_fee_payments_role", table_name="fee_payments")
        op.drop_table("fee_payments")
