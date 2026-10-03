"""назначение на роль — голос, а не запись поля

Владелец студии и автор смотрят на роль с разных сторон: один думает о загрузке и
сроках, другой — о том, как персонаж должен звучать. Голос автора весит два, голос
владельца один, и это способ разрешить спор: книгу написал он.

Голос остаётся, а решение пересчитывается из всех голосов сразу — одной колонки «кто
последний нажал» на это бы не хватило. `characters.actor_name` по-прежнему хранит
итог: всё, что читает роль ниже по течению, менять не пришлось.

Аддитивно и с проверкой, как и предыдущие ревизии.

Revision ID: 0017_role_votes
Revises: 0016_audio_kind
Create Date: 2026-09-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_role_votes"
down_revision: Union[str, None] = "0016_audio_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "role_votes" not in _tables():
        op.create_table(
            "role_votes",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("character_id", sa.String(36), nullable=False),
            sa.Column("voter_uid", sa.String(36), nullable=False),
            sa.Column("voter_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("actor_name", sa.String(120), nullable=False, server_default=""),
            sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_role_votes_character", "role_votes", ["character_id"])
        op.create_index("uq_role_votes_character_voter", "role_votes", ["character_id", "voter_uid"], unique=True)


def downgrade() -> None:
    if "role_votes" in _tables():
        op.drop_index("uq_role_votes_character_voter", table_name="role_votes")
        op.drop_index("ix_role_votes_character", table_name="role_votes")
        op.drop_table("role_votes")
