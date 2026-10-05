"""Ключи нейросетей и модели шагов — на сайте.

Revision ID: 0044_provider_keys_step_models
Revises: 0043_bot_contacts
"""
import sqlalchemy as sa
from alembic import op

revision = "0044_provider_keys_step_models"
down_revision = "0043_bot_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "provider_keys" not in tables:
        op.create_table(
            "provider_keys",
            sa.Column("provider", sa.String(20), primary_key=True),
            sa.Column("ciphertext", sa.String(), nullable=False, server_default=""),
            sa.Column("last4", sa.String(4), nullable=False, server_default=""),
            sa.Column("check_status", sa.String(20), nullable=False, server_default=""),
            sa.Column("check_detail", sa.String(200), nullable=False, server_default=""),
            sa.Column("checked_at", sa.DateTime(), nullable=True),
            sa.Column("updated_by", sa.String(120), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        )
    if "step_models" not in tables:
        op.create_table(
            "step_models",
            sa.Column("step", sa.String(40), primary_key=True),
            sa.Column("provider", sa.String(20), nullable=False, server_default=""),
            sa.Column("model", sa.String(160), nullable=False, server_default=""),
            sa.Column("updated_by", sa.String(120), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        )


def downgrade() -> None:
    op.drop_table("step_models")
    op.drop_table("provider_keys")
