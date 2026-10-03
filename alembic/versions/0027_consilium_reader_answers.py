"""ответы чтецов порознь: без них находку «чтецы врозь» нечем показать и нечем принять

`ConsiliumFinding` хранил только ОБЩЕЕ мнение чтецов. У рода `readers_split` общего
мнения нет по определению: два независимых чтеца прочли абзац по-разному, и всё
содержание находки — именно эта пара имён. Без неё строка сообщает человеку «сейчас
Дгарнин» и больше ничего, а кнопка «принять» отказывает всегда: применять нечего.

Две графы вместо одной на двоих — чтобы не гадать, чей ответ первый: имя чтеца
стоит в имени графы. Старые строки остаются с пустыми графами, и это честно: у них
ответы порознь не сохранялись вовсе, и миграция их не восстановит.

Аддитивно и с проверкой наличия графы, как предыдущие ревизии: миграции применяются
сами при старте службы, повторный прогон не должен падать.

Revision ID: 0027_consilium_reader_answers
Revises: 0026_consilium_findings
Create Date: 2026-09-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0027_consilium_reader_answers"
down_revision: Union[str, None] = "0026_consilium_findings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = ("reader_opus", "reader_sol")


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    for column in COLUMNS:
        if not _has_column("consilium_findings", column):
            op.add_column("consilium_findings",
                          sa.Column(column, sa.String(200), nullable=False, server_default=""))


def downgrade() -> None:
    for column in COLUMNS:
        if _has_column("consilium_findings", column):
            op.drop_column("consilium_findings", column)
