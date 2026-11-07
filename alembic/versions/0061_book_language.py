"""Язык книги сохраняет русское поведение прежних установок."""
import sqlalchemy as sa
from alembic import op

revision = "0061_book_language"
down_revision = "0060_asr_audits"
branch_labels = None
depends_on = None
TABLES = ("script_books", "asr_jobs", "asr_audits")


def upgrade():
    for table in TABLES:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "language" not in columns:
            op.add_column(
                table,
                sa.Column(
                    "language", sa.String(2),
                    sa.CheckConstraint("language IN ('ru', 'en')", name=f"ck_{table}_language"),
                    nullable=False, server_default="ru",
                ),
            )


def downgrade():
    for table in reversed(TABLES):
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "language" in columns:
            checks = {check["name"] for check in sa.inspect(op.get_bind()).get_check_constraints(table)}
            with op.batch_alter_table(table) as batch:
                if f"ck_{table}_language" in checks:
                    batch.drop_constraint(f"ck_{table}_language", type_="check")
                batch.drop_column("language")
