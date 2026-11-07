"""Явная идентичность ролей новых EN книг; старые данные остаются legacy."""
import sqlalchemy as sa
from alembic import op

revision = "0063_typed_role_identity"
down_revision = "0062_studio_calendar"
branch_labels = None
depends_on = None


def upgrade():
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("script_books")}
    if "role_identity_mode" not in columns:
        op.add_column("script_books", sa.Column("role_identity_mode", sa.String(16),
                  sa.CheckConstraint("role_identity_mode IN ('legacy', 'typed_v1')", name="ck_script_books_role_identity_mode"),
                  nullable=False, server_default="legacy"))
    for table in ("v2_attributions", "audio_files", "asr_jobs"):
        columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
        if "role_ref" not in columns:
            op.add_column(table, sa.Column("role_ref", sa.String(80), nullable=False, server_default=""))
    if "role_book_id" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("audio_files")}:
        op.add_column("audio_files", sa.Column("role_book_id", sa.String(36), nullable=False, server_default=""))


def downgrade():
    # Отказ от режима потерял бы смысл буквального Narrator; downgrade требует
    # явного экспорта/решения владельца, а не тихого склеивания двух ролей.
    typed = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM script_books WHERE role_identity_mode='typed_v1'")).scalar()
    referenced = any(op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE role_ref != ''")).scalar()
                     for table in ("v2_attributions", "audio_files", "asr_jobs"))
    anchored = op.get_bind().execute(sa.text("SELECT COUNT(*) FROM audio_files WHERE role_book_id != ''")).scalar()
    if typed or referenced or anchored:
        raise RuntimeError("typed_role_identity_in_use")
    if "role_book_id" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("audio_files")}:
        op.drop_column("audio_files", "role_book_id")
    for table in ("asr_jobs", "audio_files", "v2_attributions"):
        if "role_ref" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}:
            op.drop_column(table, "role_ref")
    if "role_identity_mode" in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("script_books")}:
        checks = {c["name"] for c in sa.inspect(op.get_bind()).get_check_constraints("script_books")}
        with op.batch_alter_table("script_books") as batch:
            if "ck_script_books_role_identity_mode" in checks:
                batch.drop_constraint("ck_script_books_role_identity_mode", type_="check")
            batch.drop_column("role_identity_mode")
