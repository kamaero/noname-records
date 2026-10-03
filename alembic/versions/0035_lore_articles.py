"""лор: статьи авторской энциклопедии и её карты

Шпаргалка диктору о мире книги. Привязана к автору, а не к книге: у Белозёровых арки и
персонажи сквозные через циклы.

Аддитивно и с проверкой наличия таблиц: миграции применяются сами при старте службы.

Revision ID: 0035_lore_articles
Revises: 0034_book_author_label
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035_lore_articles"
down_revision: Union[str, None] = "0034_book_author_label"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_table("lore_articles"):
        op.create_table(
            "lore_articles",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("author_id", sa.String(length=36), nullable=False),
            sa.Column("topic", sa.String(length=200), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("image_keys", sa.Text(), nullable=False, server_default=""),
            sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_lore_articles_author_topic", "lore_articles", ["author_id", "topic"], unique=True)
    if not _has_table("lore_images"):
        op.create_table(
            "lore_images",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("author_id", sa.String(length=36), nullable=False),
            sa.Column("image_key", sa.String(length=120), nullable=False),
            sa.Column("content_type", sa.String(length=60), nullable=False, server_default="image/jpeg"),
            sa.Column("stored_path", sa.String(length=512), nullable=False, server_default=""),
            sa.Column("bytes_len", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_lore_images_author_key", "lore_images", ["author_id", "image_key"], unique=True)


def downgrade() -> None:
    if _has_table("lore_images"):
        op.drop_table("lore_images")
    if _has_table("lore_articles"):
        op.drop_table("lore_articles")
