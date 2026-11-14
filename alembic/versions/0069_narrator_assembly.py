"""Рабочие версии проекта сохраняют путь независимо от прежнего ручного файла."""
from alembic import op
import sqlalchemy as sa

revision = "0069_narrator_assembly"
down_revision = "0068_chapter_titles"
branch_labels = None
depends_on = None


def upgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("script_chapters")}
    for name, length in (("session_key", 700), ("session_location", 10), ("session_input_sha256", 64)):
        if name not in existing:
            op.add_column("script_chapters", sa.Column(name, sa.String(length), nullable=False, server_default=""))

    if sa.inspect(op.get_bind()).has_table("audio_files"):
        existing_audio = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("audio_files")}
        if "mirror_proof_json" not in existing_audio:
            op.add_column("audio_files", sa.Column("mirror_proof_json", sa.Text(), nullable=False, server_default=""))


def downgrade():
    if sa.inspect(op.get_bind()).has_table("audio_files"):
        existing_audio = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("audio_files")}
        if "mirror_proof_json" in existing_audio:
            op.drop_column("audio_files", "mirror_proof_json")
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("script_chapters")}
    for name in ("session_input_sha256", "session_location", "session_key"):
        if name in existing:
            op.drop_column("script_chapters", name)
