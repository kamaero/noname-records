"""таблица поручений фоновому циклу зеркала на стирание копии с NAS

Строка `audio_files` удаляется сразу и насовсем — владелец выбрал «сразу
насовсем». Но фоновому циклу зеркала нужно знать, какой ключ хранения стереть
на NAS, а спрашивать будет уже некого: строки, у которой можно было бы это
спросить, к тому моменту нет. Отдельная маленькая таблица — не корзина и не
история удалений, а поручение, которое живёт минуты и исчезает, как только
цикл его отработал.

Аддитивно и с проверкой наличия таблицы перед созданием, как и предыдущие
ревизии: миграции этого проекта применяются сами при старте службы, повторный
прогон не должен падать.

Revision ID: 0024_pending_mirror_deletions
Revises: 0023_chapter_coverage_report
Create Date: 2026-09-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0024_pending_mirror_deletions"
down_revision: Union[str, None] = "0023_chapter_coverage_report"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "pending_mirror_deletions" not in _tables():
        op.create_table(
            "pending_mirror_deletions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("stored_key", sa.String(512), nullable=False),
            sa.Column("book_code", sa.String(40), nullable=False, server_default=""),
            sa.Column("deleted_by", sa.String(120), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    if "pending_mirror_deletions" in _tables():
        op.drop_table("pending_mirror_deletions")
