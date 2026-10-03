"""карта персонажей режиссёра: возраст роли, отметка автора о проверке, заметки о паре

Возраст — единственное поле, которого в базе не было вовсе: свободный текст,
а не число, «около сорока» вместо цифры, для подбора голоса, а не арифметики.
Отметка проверки живёт у книги, а не у строки — карта возвращается снова и
снова, и это не шаг конвейера, а состояние доверия ко всей таблице разом.
Заметки о паре ролей — под будущее автосравнение персонажей одного актёра.

Аддитивно и с проверкой наличия колонки перед добавлением: миграции этого
проекта применяются сами при старте службы, повторный прогон не должен падать.

Revision ID: 0022_character_map_tool
Revises: 0021_audio_mirror
Create Date: 2026-09-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_character_map_tool"
down_revision: Union[str, None] = "0021_audio_mirror"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    characters = _columns("characters")
    if characters and "age" not in characters:
        op.add_column("characters", sa.Column("age", sa.String(120), nullable=False, server_default=""))

    books = _columns("script_books")
    if books and "cast_checked_at" not in books:
        op.add_column("script_books", sa.Column("cast_checked_at", sa.DateTime(), nullable=True))
    if books and "cast_checked_by" not in books:
        op.add_column("script_books", sa.Column("cast_checked_by", sa.String(120), nullable=False, server_default=""))

    if "role_pair_notes" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "role_pair_notes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("book_id", sa.String(36), nullable=False),
            sa.Column("role_a", sa.String(255), nullable=False),
            sa.Column("role_b", sa.String(255), nullable=False),
            sa.Column("reason", sa.String(), nullable=False, server_default=""),
            sa.Column("actor_uid", sa.String(36), nullable=False, server_default=""),
            sa.Column("actor_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_role_pair_notes_book", "role_pair_notes", ["book_id"])


def downgrade() -> None:
    if "role_pair_notes" in set(sa.inspect(op.get_bind()).get_table_names()):
        op.drop_table("role_pair_notes")

    books = _columns("script_books")
    for name in ("cast_checked_by", "cast_checked_at"):
        if name in books:
            op.drop_column("script_books", name)

    if "age" in _columns("characters"):
        op.drop_column("characters", "age")
