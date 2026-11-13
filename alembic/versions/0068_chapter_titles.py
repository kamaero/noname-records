"""Отдельный диктор названий глав на уровне книги."""
from alembic import op
import sqlalchemy as sa
revision = "0068_chapter_titles"
down_revision = "0067_youtube_soundtrack"
branch_labels = None
depends_on = None


def upgrade():
    if "chapter_title_actor_name" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("script_books")}:
        op.add_column("script_books", sa.Column("chapter_title_actor_name", sa.String(120), nullable=False, server_default=""))


def downgrade():
    if "chapter_title_actor_name" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("script_books")}:
        op.drop_column("script_books", "chapter_title_actor_name")
