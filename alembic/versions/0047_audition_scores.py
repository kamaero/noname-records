"""пробы: оценка 1–10 на пару «актёр + роль» вместо 👍/👎 на файл

Ждущие отказы по старым 👎 отменяются здесь же: с этой ревизии отказ заводит только
утверждение роли, и таймер не должен разослать письма по отменённой механике в первую
же минуту после выкатки. Отправленные и прожитые отказы остаются историей.

Revision ID: 0044_audition_scores
Revises: 0043_bot_contacts
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0047_audition_scores"
down_revision: Union[str, None] = "0046_onboarding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> list[str]:
    return sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    tables = _tables()
    if "audition_scores" not in tables:
        op.create_table(
            "audition_scores",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("book_id", sa.String(length=36), nullable=False),
            sa.Column("role", sa.String(length=120), nullable=False),
            sa.Column("actor_name", sa.String(length=120), nullable=False),
            sa.Column("lane", sa.String(length=10), nullable=False),
            sa.Column("voter_uid", sa.String(length=36), nullable=False),
            sa.Column("voter_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("score", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("book_id", "role", "actor_name", "lane", name="uq_audition_scores_pair_lane"),
        )
        op.create_index("ix_audition_scores_book", "audition_scores", ["book_id"])
    if "audition_rejections" in tables:
        op.execute(sa.text(
            "UPDATE audition_rejections SET status = 'cancelled' WHERE status IN ('pending', 'sending')"
        ))
    if "audition_reactions" in tables:
        op.drop_table("audition_reactions")


def downgrade() -> None:
    tables = _tables()
    if "audition_reactions" not in tables:
        op.create_table(
            "audition_reactions",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("audio_file_id", sa.String(length=36), nullable=False),
            sa.Column("voter_uid", sa.String(length=36), nullable=False),
            sa.Column("voter_name", sa.String(length=120), nullable=False, server_default=""),
            sa.Column("value", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("audio_file_id", "voter_uid", name="uq_audition_reactions_audio_voter"),
        )
        op.create_index("ix_audition_reactions_audio", "audition_reactions", ["audio_file_id"])
    if "audition_scores" in tables:
        op.drop_table("audition_scores")
