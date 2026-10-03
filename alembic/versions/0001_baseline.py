"""baseline schema

Baseline revision capturing the full current schema. On a fresh database it
builds every table from the SQLAlchemy models (Base.metadata). The legacy
idempotent apply_additive_migrations() still runs at startup as a safety net for
indexes/columns during the transition; future schema changes get their own
Alembic revisions on top of this baseline.

The existing production DB was marked with `alembic stamp head`, so this baseline
never re-runs there.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-29

"""
from typing import Sequence, Union

from alembic import op

# Importing the models package populates Base.metadata.
import app.models  # noqa: F401
from app.db import Base

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
