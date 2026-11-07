"""Профиль оплачиваемого прогона отделён от редактируемого языка книги."""
import sqlalchemy as sa
from alembic import op

revision = "0064_v2_run_language_snapshot"
down_revision = "0063_typed_role_identity"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "v2_runs" not in sa.inspect(bind).get_table_names():
        return
    if "language_snapshot" not in {c["name"] for c in sa.inspect(bind).get_columns("v2_runs")}:
        op.add_column("v2_runs", sa.Column("language_snapshot", sa.String(2), nullable=True))


def downgrade():
    bind = op.get_bind()
    if "v2_runs" in sa.inspect(bind).get_table_names():
        if "language_snapshot" in {c["name"] for c in sa.inspect(bind).get_columns("v2_runs")}:
            with op.batch_alter_table("v2_runs") as batch:
                batch.drop_column("language_snapshot")
