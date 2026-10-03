"""автор книги отдельной графой — чтобы не течь в название

`title` кормит код книги (КП, СВК2), который актёры пишут в именах файлов; автор,
попавший туда при загрузке, этот код молча портит. Витрина «Автор - "Название"»
теперь собирается в `display_title` из `title` + `author_label`.

Аддитивно и с проверкой наличия графы: миграции применяются сами при старте службы.

Revision ID: 0034_book_author_label
Revises: 0033_user_onboarding
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0034_book_author_label"
down_revision: Union[str, None] = "0033_user_onboarding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("script_books", "author_label"):
        op.add_column(
            "script_books",
            sa.Column("author_label", sa.String(length=120), nullable=False, server_default=""),
        )


def downgrade() -> None:
    if _has_column("script_books", "author_label"):
        op.drop_column("script_books", "author_label")
