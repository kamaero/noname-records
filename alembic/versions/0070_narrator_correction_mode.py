"""Новые полные пересъёмки не переосмысляют исторические короткие фиксы."""
from alembic import op
import sqlalchemy as sa

revision = "0070_narrator_correction_mode"
down_revision = "0069_narrator_assembly"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("audio_files", "bot_uploads"):
        inspector = sa.inspect(op.get_bind())
        if inspector.has_table(table) and "correction_mode" not in {c["name"] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column("correction_mode", sa.String(16), nullable=False, server_default=""))
    if sa.inspect(op.get_bind()).has_table("audio_files") and "correction_source_id" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("audio_files")}:
        op.add_column("audio_files", sa.Column("correction_source_id", sa.String(36), nullable=False, server_default=""))


def downgrade():
    if sa.inspect(op.get_bind()).has_table("audio_files") and "correction_source_id" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("audio_files")}:
        op.drop_column("audio_files", "correction_source_id")
    for table in ("bot_uploads", "audio_files"):
        inspector = sa.inspect(op.get_bind())
        if inspector.has_table(table) and "correction_mode" in {c["name"] for c in inspector.get_columns(table)}:
            op.drop_column(table, "correction_mode")
