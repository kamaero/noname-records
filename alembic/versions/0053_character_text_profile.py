"""Характеристика роли по тексту книги — черновик нейросети (07.10)

У половины ролей «Карты» нет ни описания в каноне, ни темперамента: диктор, позванный
на пробу, видел только «человек» и заметку о голосе. Характеристику по репликам пишет
модель; хранится отдельно от полей автора и заметки студии, а `text_profile_by` говорит,
чья она: «model» — черновик (Читалка так и помечает), «human» — поправлена руками.

Revision ID: 0050_character_text_profile
Revises: 0049_fee_payments
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0053_character_text_profile"
down_revision: Union[str, None] = "0052_fee_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("characters")}


def upgrade() -> None:
    have = _columns()
    with op.batch_alter_table("characters") as batch:
        if "text_profile" not in have:
            batch.add_column(sa.Column("text_profile", sa.Text(), nullable=False, server_default=""))
        if "text_profile_by" not in have:
            batch.add_column(sa.Column("text_profile_by", sa.String(16), nullable=False, server_default=""))


def downgrade() -> None:
    have = _columns()
    with op.batch_alter_table("characters") as batch:
        for name in ("text_profile_by", "text_profile"):
            if name in have:
                batch.drop_column(name)
