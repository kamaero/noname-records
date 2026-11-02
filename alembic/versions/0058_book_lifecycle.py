"""Жизненный цикл книги: «Глава сдана», «Озвучена» и учёт благодарностей

`script_chapters.released_at` — студия отметила главу сданной заказчику (не путать с
`delivered_at`, «всё записано»). `script_books.voiced_at` — книга объявлена озвученной.
`book_thanks_sent` помнит, кому за книгу уже ушло письмо, чтобы повторное «озвучена» не
слало его второй раз. Проверки идемпотентны: повторный прогон ничего не ломает.

Revision ID: 0055_book_lifecycle
Revises: 0054_bot_takes
Create Date: 2026-10-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0058_book_lifecycle"
down_revision: Union[str, None] = "0057_bot_takes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (("script_chapters", "released_at"), ("script_books", "voiced_at"))


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    for table, column in COLUMNS:
        if table in tables and column not in {c["name"] for c in inspector.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column(column, sa.DateTime(), nullable=True))
    if "book_thanks_sent" not in tables:
        op.create_table(
            "book_thanks_sent",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("telegram_user_id", sa.String(32), nullable=False),
            sa.Column("actor_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("sent_at", sa.DateTime(), nullable=False),
            sa.Column("delivered", sa.Boolean(), nullable=False, server_default="0"),
            sa.UniqueConstraint("book_id", "telegram_user_id", name="uq_book_thanks_sent"),
        )
    elif "delivered" not in {c["name"] for c in inspector.get_columns("book_thanks_sent")}:
        # Среда, где таблица успела появиться без итога доставки: «ушло или нет» не
        # узнать, поэтому прежние строки считаем недоставленными.
        with op.batch_alter_table("book_thanks_sent") as batch:
            batch.add_column(sa.Column("delivered", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "book_thanks_sent" in tables:
        op.drop_table("book_thanks_sent")
    for table, column in COLUMNS:
        if table in tables and column in {c["name"] for c in inspector.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.drop_column(column)
