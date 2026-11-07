"""Выбор саундтрека и неизменяемые снимки кредитов главы."""
import sqlalchemy as sa
from alembic import op

revision = "0066_soundtrack"
down_revision = "0065_ambient_language_snapshot"
branch_labels = None
depends_on = None

TABLES = ("soundtrack_states", "soundtrack_works", "soundtrack_entries",
          "soundtrack_rejections", "soundtrack_snapshots", "soundtrack_requests")


def _tables():
    # История миграций не зависит от будущих изменений ORM-моделей.
    metadata = sa.MetaData()

    def identity():
        return sa.Column("id", sa.String(36), primary_key=True)

    def book():
        return sa.Column("book_id", sa.String(36), nullable=False, index=True)

    def chapter():
        return sa.Column("chapter_id", sa.String(36), nullable=False, index=True)

    sa.Table("soundtrack_states", metadata,
        sa.Column("chapter_id", sa.String(36), primary_key=True), book(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=True))
    sa.Table("soundtrack_works", metadata, identity(), book(),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("identity", sa.String(100), nullable=False),
        sa.Column("original", sa.JSON(), nullable=False),
        sa.UniqueConstraint("book_id", "source", "identity", name="uq_soundtrack_work_identity"))
    sa.Table("soundtrack_entries", metadata, identity(), book(), chapter(),
        sa.Column("work_id", sa.String(36), nullable=False),
        sa.Column("scene_id", sa.String(36), nullable=False),
        sa.Column("scene_title", sa.String(300), nullable=False),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False),
        sa.Column("removed", sa.Boolean(), nullable=False),
        sa.Column("overrides", sa.JSON(), nullable=False),
        sa.UniqueConstraint("chapter_id", "work_id", "scene_id", "layer", name="uq_soundtrack_entry_use"))
    sa.Table("soundtrack_rejections", metadata, identity(), book(), chapter(),
        sa.Column("scene_id", sa.String(36), nullable=False),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("openverse_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("chapter_id", "scene_id", "layer", "openverse_id", name="uq_soundtrack_rejection"))
    sa.Table("soundtrack_snapshots", metadata, identity(), book(), chapter(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("entries", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False))
    sa.Table("soundtrack_requests", metadata, identity(), book(), chapter(),
        sa.Column("request_id", sa.String(80), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.UniqueConstraint("chapter_id", "request_id", name="uq_soundtrack_request"))
    return metadata.tables


def upgrade():
    tables = _tables()
    for name in TABLES:
        tables[name].create(op.get_bind(), checkfirst=True)


def downgrade():
    tables = _tables()
    for name in reversed(TABLES):
        tables[name].drop(op.get_bind(), checkfirst=True)
