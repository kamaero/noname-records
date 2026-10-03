"""авторские иллюстрации книги и привязка их к персонажам

В fb2 «Крыльев» 190 иллюстраций, и ни одна не подписана: кто изображён, видно только из
текста вокруг. Строка держит свой контекст, по нему экран привязки предлагает кандидатов
из каста, а решает человек.

Аддитивно и с проверкой наличия таблицы: миграции применяются сами при старте службы.

Revision ID: 0036_book_illustrations
Revises: 0035_lore_articles
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036_book_illustrations"
down_revision: Union[str, None] = "0035_lore_articles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("book_illustrations"):
        return
    op.create_table(
        "book_illustrations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("book_id", sa.String(length=36), nullable=False),
        sa.Column("author_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("image_key", sa.String(length=120), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stored_path", sa.String(length=512), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(length=60), nullable=False, server_default="image/jpeg"),
        sa.Column("bytes_len", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("context", sa.Text(), nullable=False, server_default=""),
        sa.Column("chapter_hint", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("character_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=12), nullable=False, server_default="new"),
        sa.Column("bound_by", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("bound_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_book_illustrations_book", "book_illustrations", ["book_id", "ordinal"])
    op.create_index("ix_book_illustrations_character", "book_illustrations", ["character_id"])


def downgrade() -> None:
    if _has_table("book_illustrations"):
        op.drop_table("book_illustrations")
