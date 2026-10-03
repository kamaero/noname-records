"""пробы на роль отличаются от дублей утверждённой роли одним полем

Дикторы начали присылать пробы вперемешку с записями утверждённых ролей. Отличить их
было нечем: в `audio_files` есть глава, роль и актёр — и ни одного места, где сказать,
что это проба. Диктор писал «пробы» в имя файла и выбирал главу наугад, потому что
форма требовала главу.

Аддитивно и с проверкой, как и предыдущие ревизии. Всё, что уже лежит в таблице, —
дубли: `server_default` проставляет им «take» без отдельного UPDATE.

Revision ID: 0016_audio_kind
Revises: 0015_stress_skips
Create Date: 2026-09-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_audio_kind"
down_revision: Union[str, None] = "0015_stress_skips"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    if "audio_files" in set(sa.inspect(op.get_bind()).get_table_names()) and "kind" not in _columns("audio_files"):
        op.add_column(
            "audio_files",
            sa.Column("kind", sa.String(16), nullable=False, server_default="take"),
        )
        op.create_index("ix_audio_files_kind", "audio_files", ["kind"])


def downgrade() -> None:
    if "kind" in _columns("audio_files"):
        op.drop_index("ix_audio_files_kind", table_name="audio_files")
        op.drop_column("audio_files", "kind")
