"""Журнал трат, цены моделей и месячный лимит.

Revision ID: 0045_spend
Revises: 0044_provider_keys_step_models
"""
import sqlalchemy as sa
from alembic import op

revision = "0045_spend"
down_revision = "0044_provider_keys_step_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "spend_entries" not in tables:
        op.create_table(
            "spend_entries",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("step", sa.String(40), nullable=False, server_default="other"),
            sa.Column("provider", sa.String(20), nullable=False, server_default=""),
            sa.Column("model", sa.String(160), nullable=False, server_default=""),
            sa.Column("book_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("chapter_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("run_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("unit", sa.String(10), nullable=False, server_default="tokens"),
            sa.Column("input_units", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_units", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("rub", sa.Float(), nullable=True),
            sa.Column("price_known", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.create_index("ix_spend_entries_created_at", "spend_entries", ["created_at"])
    if "model_prices" not in tables:
        op.create_table(
            "model_prices",
            sa.Column("provider", sa.String(20), primary_key=True),
            sa.Column("model", sa.String(160), primary_key=True),
            sa.Column("unit", sa.String(10), nullable=False, server_default="tokens"),
            sa.Column("price_in", sa.Float(), nullable=True),
            sa.Column("price_out", sa.Float(), nullable=True),
            sa.Column("price_unit", sa.Float(), nullable=True),
            sa.Column("currency", sa.String(3), nullable=False, server_default="RUB"),
            sa.Column("updated_by", sa.String(120), nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.current_timestamp()),
        )
    if "spend_holds" not in tables:
        op.create_table(
            "spend_holds",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("estimate_rub", sa.Float(), nullable=False, server_default="0"),
            sa.Column("what", sa.String(80), nullable=False, server_default=""),
        )
        op.create_index("ix_spend_holds_created_at", "spend_holds", ["created_at"])
    if "studio_settings" in tables:
        columns = {c["name"] for c in inspector.get_columns("studio_settings")}
        if "monthly_limit_rub" not in columns:
            op.add_column("studio_settings", sa.Column("monthly_limit_rub", sa.Integer(), nullable=False, server_default="0"))
        if "spend_warned_month" not in columns:
            op.add_column("studio_settings", sa.Column("spend_warned_month", sa.String(16), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_index("ix_spend_holds_created_at", table_name="spend_holds")
    op.drop_table("spend_holds")
    op.drop_table("model_prices")
    op.drop_index("ix_spend_entries_created_at", table_name="spend_entries")
    op.drop_table("spend_entries")
