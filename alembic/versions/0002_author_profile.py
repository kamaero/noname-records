"""author profile foundation

Revision ID: 0002_author_profile
Revises: 0001_baseline
Create Date: 2026-05-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_author_profile"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return name in _insp().get_table_names()


def _has_column(table: str, column: str) -> bool:
    return _has_table(table) and column in {c["name"] for c in _insp().get_columns(table)}


def upgrade() -> None:
    if not _has_table("authors"):
        op.create_table(
            "authors",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False, server_default=""),
            sa.Column("slug", sa.String(120), nullable=False, server_default=""),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_authors_slug", "authors", ["slug"])
    if not _has_table("author_characters"):
        op.create_table(
            "author_characters",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("author_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("canonical_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("aliases", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("reply_color", sa.String(20), nullable=False, server_default=""),
            sa.Column("actor_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("actor_user_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("source_topic", sa.String(255), nullable=False, server_default=""),
            sa.Column("appears_in_books", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("status", sa.String(20), nullable=False, server_default="unconfirmed"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_author_characters_author_id", "author_characters", ["author_id"])
        op.create_index("ix_author_characters_author_name", "author_characters", ["author_id", "canonical_name"])
    if not _has_table("author_pronunciations"):
        op.create_table(
            "author_pronunciations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("author_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("term", sa.String(255), nullable=False, server_default=""),
            sa.Column("stressed", sa.String(255), nullable=False, server_default=""),
            sa.Column("variants", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("source", sa.String(40), nullable=False, server_default="compendium"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_author_pronunciations_author_id", "author_pronunciations", ["author_id"])
    if not _has_column("script_books", "author_id"):
        op.add_column("script_books", sa.Column("author_id", sa.String(36), nullable=False, server_default=""))
    if not _has_column("characters", "author_character_id"):
        op.add_column("characters", sa.Column("author_character_id", sa.String(36), nullable=False, server_default=""))


def downgrade() -> None:
    for tbl in ("author_pronunciations", "author_characters", "authors"):
        if _has_table(tbl):
            op.drop_table(tbl)
