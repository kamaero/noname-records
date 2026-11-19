"""Готовые варианты сцен не меняют прежние треки ElevenLabs."""
from alembic import op
import sqlalchemy as sa

revision = "0071_ambient_pairs"
down_revision = "0070_narrator_correction_mode"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("ambient_tracks"):
        return
    existing = {column["name"] for column in inspector.get_columns("ambient_tracks")}
    for name, kind in (("variant", sa.String(1)), ("pair_key", sa.String(64)), ("scene_fingerprint", sa.String(64))):
        if name not in existing:
            op.add_column("ambient_tracks", sa.Column(name, kind, nullable=False, server_default=""))


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("ambient_tracks"):
        existing = {column["name"] for column in inspector.get_columns("ambient_tracks")}
        with op.batch_alter_table("ambient_tracks") as batch:
            for name in ("scene_fingerprint", "pair_key", "variant"):
                if name in existing:
                    batch.drop_column(name)
