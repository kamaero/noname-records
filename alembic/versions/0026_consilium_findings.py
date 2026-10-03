"""таблица находок консилиума

Результат прогона перестаёт быть файлом на диске и становится состоянием системы:
находку видно в интерфейсе, и её можно принять или отклонить. Решение человека
хранится отдельно от самого спора, поэтому повторный прогон не воскрешает то, что
уже разобрано.

Аддитивно и с проверкой наличия таблицы, как предыдущие ревизии: миграции
применяются сами при старте службы, повторный прогон не должен падать.

Revision ID: 0026_consilium_findings
Revises: 0025_asr_heard_json
Create Date: 2026-09-12
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0026_consilium_findings"
down_revision: Union[str, None] = "0025_asr_heard_json"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "consilium_findings" in _tables():
        return
    op.create_table(
        "consilium_findings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("book_id", sa.String(36), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("segment_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("span_start", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("span_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(20), nullable=False, server_default="wrong_voice"),
        sa.Column("current_speaker", sa.String(200), nullable=False, server_default=""),
        sa.Column("readers_speaker", sa.String(200), nullable=False, server_default=""),
        sa.Column("arbiter_verdict", sa.String(20), nullable=False, server_default=""),
        sa.Column("arbiter_speaker", sa.String(200), nullable=False, server_default=""),
        sa.Column("evidence_para", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence_proven", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="new"),
        sa.Column("decided_by", sa.String(120), nullable=False, server_default=""),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("run_label", sa.String(40), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_consilium_findings_book", "consilium_findings", ["book_id", "status"])
    op.create_index("ix_consilium_findings_segment", "consilium_findings", ["segment_id"])


def downgrade() -> None:
    if "consilium_findings" in _tables():
        op.drop_table("consilium_findings")
