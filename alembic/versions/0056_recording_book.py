"""настройки студии: книга, которая сейчас записывается

Бот принимает дубли только по одной книге, и какая она — решение студии, а не догадка
бота. Поле одно на студию. При выкатке сразу ставим «Карта Приозерья», чтобы бот
заработал без ручного шага.

Revision ID: 0053_recording_book
Revises: 0052_bot_demos
Create Date: 2026-10-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0056_recording_book"
down_revision: Union[str, None] = "0055_bot_demos"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_TITLE = ""


def set_default_recording_book(conn) -> None:
    """Отдельная функция, чтобы проверять на SQLite в памяти без Alembic."""
    # Строки настроек может не быть: она создаётся лениво с пустой книгой, и владелец
    # включает нужную в хабе, так что пропуск намеренный.
    row_exists = conn.execute(sa.text("SELECT 1 FROM studio_settings WHERE id = 'default'")).first()
    if row_exists is None:
        return
    for book_id, title in conn.execute(sa.text("SELECT id, title FROM script_books")).fetchall():
        if str(title or "").strip() == DEFAULT_TITLE:
            conn.execute(sa.text("UPDATE studio_settings SET recording_book_id = :b "
                                 "WHERE id = 'default' AND COALESCE(recording_book_id, '') = ''"),
                         {"b": book_id})
            return


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "studio_settings" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("studio_settings")}
    if "recording_book_id" not in columns:
        with op.batch_alter_table("studio_settings") as batch:
            batch.add_column(sa.Column("recording_book_id", sa.String(36), nullable=False, server_default=""))
    set_default_recording_book(conn)


def downgrade() -> None:
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("studio_settings")}
    if "recording_book_id" in columns:
        with op.batch_alter_table("studio_settings") as batch:
            batch.drop_column("recording_book_id")
