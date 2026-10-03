"""графа с услышанным: сверку можно пересчитать, не платя за распознавание заново

Распознавание — единственный платный и невоспроизводимый шаг пути дубля: робот
слушает файл один раз. Сверка со сценарием, наоборот, своя, бесплатная и
меняется постоянно. Пока в базе оставался только её ИТОГ (`alignment_json`), а
её ВХОД — сегменты со словными таймингами — выбрасывался, каждая правка сверки
доставалась только будущим записям; архив можно было переиграть лишь повторной
оплатой распознавания.

Старые строки остаются с пустой графой, и это честно: у них вход выброшен, и
никакая миграция его не вернёт. Переигрываемыми становятся записи, распознанные
после этой ревизии.

Аддитивно и с проверкой наличия графы, как и предыдущие ревизии: миграции
применяются сами при старте службы, повторный прогон не должен падать.

Revision ID: 0025_asr_heard_json
Revises: 0024_pending_mirror_deletions
Create Date: 2026-09-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0025_asr_heard_json"
down_revision: Union[str, None] = "0024_pending_mirror_deletions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("asr_jobs", "heard_json"):
        op.add_column("asr_jobs", sa.Column("heard_json", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    if _has_column("asr_jobs", "heard_json"):
        op.drop_column("asr_jobs", "heard_json")
