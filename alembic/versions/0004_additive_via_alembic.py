"""run legacy additive migrations as a versioned step (retire startup coupling)

The legacy idempotent `apply_additive_migrations()` (ADD COLUMN where missing, CREATE INDEX
IF NOT EXISTS for ~50 indexes, one-off backfills like char_map_id) used to run on EVERY app
startup alongside Alembic. It now runs once here, versioned, so Alembic is the single schema
authority and startup no longer calls it. Idempotent, so safe on the already-migrated prod DB.

Revision ID: 0004_additive_via_alembic
Revises: 0003_stress_report
Create Date: 2026-05-29
"""
from typing import Sequence, Union

revision: str = "0004_additive_via_alembic"
down_revision: Union[str, None] = "0003_stress_report"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db import apply_additive_migrations

    apply_additive_migrations()


def downgrade() -> None:
    pass
