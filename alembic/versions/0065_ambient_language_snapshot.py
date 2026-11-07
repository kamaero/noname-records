"""Сохранённая музыка помнит профиль, по которому был оплачен её промпт."""
import sqlalchemy as sa
from alembic import op

revision = "0065_ambient_language_snapshot"
down_revision = "0064_v2_run_language_snapshot"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "ambient_tracks" not in sa.inspect(bind).get_table_names():
        return
    if "language_snapshot" not in {c["name"] for c in sa.inspect(bind).get_columns("ambient_tracks")}:
        op.add_column("ambient_tracks", sa.Column("language_snapshot", sa.String(2), nullable=False, server_default="ru"))


def downgrade():
    bind = op.get_bind()
    if "ambient_tracks" in sa.inspect(bind).get_table_names():
        if "language_snapshot" in {c["name"] for c in sa.inspect(bind).get_columns("ambient_tracks")}:
            with op.batch_alter_table("ambient_tracks") as batch:
                batch.drop_column("language_snapshot")
