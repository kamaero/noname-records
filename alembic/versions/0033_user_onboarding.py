"""отметка «приветственное окно уже показывали» на учётке диктора

Сервер сам открывает окно новичку и тому, до кого пока не достучался бот; после
автопоказа ставит сюда `now`, чтобы не показывать второй раз без причины.

Аддитивно и с проверкой наличия графы: миграции применяются сами при старте службы.

Revision ID: 0033_user_onboarding
Revises: 0032_ambient_tracks
Create Date: 2026-09-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0033_user_onboarding"
down_revision: Union[str, None] = "0032_ambient_tracks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return table in insp.get_table_names() and column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_column("users", "onboarding_shown_at"):
        op.add_column("users", sa.Column("onboarding_shown_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "onboarding_shown_at"):
        op.drop_column("users", "onboarding_shown_at")
