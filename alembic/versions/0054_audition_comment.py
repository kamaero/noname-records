"""Комментарий автора к пробе и ожидание его в боте (11.10)

Пяволов не называл причину низкой оценки: кнопка «голос» читалась как «голос плохой»,
а он хотел сказать «голос отличный, но не этому герою», и главное — каким герой должен
звучать — сказать было негде. Комментарий своими словами живёт на паре «актёр + роль»
рядом с оценкой и виден только студии; `bot_comment_waits` — что бот ждёт его текстом.

Revision ID: 0051_audition_comment
Revises: 0050_character_text_profile
Create Date: 2026-10-11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0054_audition_comment"
down_revision: Union[str, None] = "0053_character_text_profile"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    insp = _inspector()
    tables = set(insp.get_table_names())
    if "audition_scores" in tables and "comment" not in {c["name"] for c in insp.get_columns("audition_scores")}:
        op.add_column("audition_scores", sa.Column("comment", sa.Text(), nullable=False, server_default=""))
    if "bot_comment_waits" not in tables:
        op.create_table(
            "bot_comment_waits",
            sa.Column("tid", sa.String(32), primary_key=True),
            sa.Column("audio_id", sa.String(36), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )


def downgrade() -> None:
    insp = _inspector()
    tables = set(insp.get_table_names())
    if "bot_comment_waits" in tables:
        op.drop_table("bot_comment_waits")
    if "audition_scores" in tables and "comment" in {c["name"] for c in insp.get_columns("audition_scores")}:
        with op.batch_alter_table("audition_scores") as batch:
            batch.drop_column("comment")
