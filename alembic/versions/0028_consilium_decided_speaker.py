"""чем кончилась находка: имя, которое применили или оставили

Без этой графы в «решённых» видно только «принято» — а что именно поставили, знает один
журнал вмешательств. Пустая строка у старых решений честна: имя тогда не записывалось.

Аддитивно и с проверкой наличия графы: миграции применяются сами при старте службы.

Revision ID: 0028_consilium_decided_speaker
Revises: 0027_consilium_reader_answers
Create Date: 2026-09-13
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0028_consilium_decided_speaker"
down_revision: Union[str, None] = "0027_consilium_reader_answers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("consilium_findings", "decided_speaker"):
        op.add_column("consilium_findings",
                      sa.Column("decided_speaker", sa.String(200), nullable=False, server_default=""))


def downgrade() -> None:
    if _has_column("consilium_findings", "decided_speaker"):
        op.drop_column("consilium_findings", "decided_speaker")
