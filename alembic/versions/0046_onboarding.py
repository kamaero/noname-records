"""«Первые шаги»: книга-пример и флаги карточки в studio_settings.

Revision ID: 0046_onboarding
Revises: 0045_spend
"""
import sqlalchemy as sa
from alembic import op

revision = "0046_onboarding"
down_revision = "0045_spend"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("sample_book_id", sa.String(36), nullable=False, server_default=""),
    sa.Column("onboarding_no_limit", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("onboarding_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("onboarding_result_seen_at", sa.DateTime(), nullable=True),
)


def _existing() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "studio_settings" not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns("studio_settings")}


def upgrade() -> None:
    have = _existing()
    with op.batch_alter_table("studio_settings") as batch:
        for column in COLUMNS:
            if column.name not in have:
                batch.add_column(column.copy())


def downgrade() -> None:
    have = _existing()
    with op.batch_alter_table("studio_settings") as batch:
        for column in reversed(COLUMNS):
            if column.name in have:
                batch.drop_column(column.name)
