"""bot_uploads: дубли из бота рядом с пробами

Бот принимает не только пробы, но и дубли книги, которая сейчас записывается. Таблица та же:
`file_id` всё так же не влезает в `callback_data`, и кнопка выбора роли несёт короткий `id`.
Назначение строки (`purpose`) отделяет дубль от пробы; у старых строк оно «audition».

Revision ID: 0054_bot_takes
Revises: 0053_recording_book
Create Date: 2026-10-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0057_bot_takes"
down_revision: Union[str, None] = "0056_recording_book"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("purpose", sa.String(10), "audition"),
    ("role_name", sa.String(120), ""),
    ("book_id", sa.String(36), ""),
    ("md5", sa.String(32), ""),
    ("result_json", sa.Text(), ""),
    ("stored_key", sa.String(255), ""),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "bot_uploads" not in inspector.get_table_names():
        return
    have = {c["name"] for c in inspector.get_columns("bot_uploads")}
    missing = [(name, kind, default) for name, kind, default in COLUMNS if name not in have]
    if not missing:
        return
    with op.batch_alter_table("bot_uploads") as batch:
        for name, kind, default in missing:
            batch.add_column(sa.Column(name, kind, nullable=False, server_default=default))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "bot_uploads" not in inspector.get_table_names():
        return
    have = {c["name"] for c in inspector.get_columns("bot_uploads")}
    present = [name for name, _, _ in COLUMNS if name in have]
    if not present:
        return
    with op.batch_alter_table("bot_uploads") as batch:
        for name in present:
            batch.drop_column(name)
