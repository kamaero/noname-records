"""Подтверждённые пользователем загрузки саундтрека.

Revision ID: 0067_youtube_soundtrack
Revises: 0066_soundtrack
"""
from alembic import op
import sqlalchemy as sa

revision = "0067_youtube_soundtrack"
down_revision = "0066_soundtrack"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("youtube_soundtrack_downloads"):
        return
    op.create_table(
        "youtube_soundtrack_downloads",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("book_id", sa.String(36), nullable=False),
        sa.Column("chapter_id", sa.String(36), nullable=False),
        sa.Column("scene_id", sa.String(36), nullable=False),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("video_id", sa.String(11), nullable=False),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("rights_note", sa.Text(), nullable=False),
        sa.Column("confirmed_by", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(80), nullable=False),
        sa.Column("audio_file_id", sa.String(36), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("chapter_id", "request_id", name="uq_youtube_download_request"),
    )
    op.create_index("ix_youtube_soundtrack_downloads_book_id", "youtube_soundtrack_downloads", ["book_id"])
    op.create_index("ix_youtube_download_chapter", "youtube_soundtrack_downloads", ["chapter_id", "created_at"])


def downgrade():
    if sa.inspect(op.get_bind()).has_table("youtube_soundtrack_downloads"):
        op.drop_table("youtube_soundtrack_downloads")
