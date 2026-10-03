"""per-book model choice: bring `llm_provider` / `llm_model` back into the mapping

The columns exist on every database that ever ran v1; a database created from the v2
models alone does not have them. Additive and guarded like 0008-0010.

Revision ID: 0011_book_model_choice
Revises: 0010_studio_settings
Create Date: 2026-09-05
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_book_model_choice"
down_revision: Union[str, None] = "0010_studio_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {row["name"] for row in sa.inspect(op.get_bind()).get_columns("script_books")}


def upgrade() -> None:
    existing = _columns()
    if "llm_provider" not in existing:
        op.add_column("script_books", sa.Column("llm_provider", sa.String(40), nullable=False, server_default=""))
    if "llm_model" not in existing:
        op.add_column("script_books", sa.Column("llm_model", sa.String(120), nullable=False, server_default=""))


def downgrade() -> None:
    # The columns predate this revision on real databases; dropping them would lose the
    # choice for a rollback that gains nothing.
    pass
