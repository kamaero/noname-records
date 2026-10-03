"""words the author has waved off: «ударение тут не нужно»

Not a stress decision — a stress term has to name a stressed vowel, and these words
have nothing to say about one. This is a statement about the queue: stop asking.

The queue holds 30 304 distinct unstressed words for «Крыльев», and the author does
not want to mark «даже» and «сказал». Waving a word off is what makes the list
shrink as he works instead of staying the same size forever.

Additive and guarded like the revisions before it.

Revision ID: 0015_stress_skips
Revises: 0014_telegram_account_link
Create Date: 2026-09-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_stress_skips"
down_revision: Union[str, None] = "0014_telegram_account_link"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "v2_stress_skips" not in _tables():
        op.create_table(
            "v2_stress_skips",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("word", sa.String(120), nullable=False),
            sa.Column("actor_uid", sa.String(36), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_v2_stress_skips_book_word", "v2_stress_skips", ["book_id", "word"], unique=True)


def downgrade() -> None:
    if "v2_stress_skips" in _tables():
        op.drop_index("ix_v2_stress_skips_book_word", table_name="v2_stress_skips")
        op.drop_table("v2_stress_skips")
