"""отметка о составе главы, по которому владельцу уже ушёл отчёт о сверке

Событие «глава собралась» в системе нигде не хранится: статус считается на
чтении. Без отметки о том, по какому составу дублей отчёт уже отправлен, каждая
следующая загрузка присылала бы владельцу один и тот же отчёт заново.

Аддитивно и с проверкой наличия колонки перед добавлением: миграции этого
проекта применяются сами при старте службы, повторный прогон не должен падать.

Revision ID: 0023_chapter_coverage_report
Revises: 0022_character_map_tool
Create Date: 2026-09-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0023_chapter_coverage_report"
down_revision: Union[str, None] = "0022_character_map_tool"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    chapters = _columns("script_chapters")
    if chapters and "coverage_reported_takes" not in chapters:
        op.add_column(
            "script_chapters",
            sa.Column("coverage_reported_takes", sa.String(64), nullable=False, server_default=""),
        )
    if chapters and "coverage_reported_at" not in chapters:
        op.add_column("script_chapters", sa.Column("coverage_reported_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    chapters = _columns("script_chapters")
    for name in ("coverage_reported_at", "coverage_reported_takes"):
        if name in chapters:
            op.drop_column("script_chapters", name)
