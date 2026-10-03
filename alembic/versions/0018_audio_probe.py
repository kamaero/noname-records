"""что сказал о себе сам аудиофайл: длительность, частота, каналы, кодек

О загруженном мы знали только размер в байтах. Размер не отличает пятнадцать минут речи
от пятнадцати минут тишины, не говорит, что диктор писал в стерео на 22 кГц, и не даёт
длительности — а она нужна и смете, и описи главы, и сборке в монтажке.

Аддитивно и с проверкой. Старым записям остаются нули: это честное «не спрашивали».

Revision ID: 0018_audio_probe
Revises: 0017_role_votes
Create Date: 2026-09-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_audio_probe"
down_revision: Union[str, None] = "0017_role_votes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    ("duration_seconds", sa.Float(), "0"),
    ("sample_rate", sa.Integer(), "0"),
    ("channels", sa.Integer(), "0"),
    ("codec", sa.String(40), ""),
)


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if "audio_files" not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns("audio_files")}


def upgrade() -> None:
    have = _columns()
    if not have:
        return
    for name, kind, default in COLUMNS:
        if name not in have:
            op.add_column("audio_files", sa.Column(name, kind, nullable=False, server_default=default))


def downgrade() -> None:
    have = _columns()
    for name, _kind, _default in COLUMNS:
        if name in have:
            op.drop_column("audio_files", name)
