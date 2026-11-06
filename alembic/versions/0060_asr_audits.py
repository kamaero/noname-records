"""Строгая проверка рассказчика: собственный след аудита независим от боевой сверки."""
import sqlalchemy as sa
from alembic import op

revision = "0060_asr_audits"
down_revision = "0059_audition_rejection_cause"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    columns = {item["name"] for item in sa.inspect(bind).get_columns("script_chapters")}
    if "audit_assembly_fingerprint" not in columns:
        op.add_column("script_chapters", sa.Column("audit_assembly_fingerprint", sa.String(64), nullable=False, server_default=""))
    if "audit_assembly_take_id" not in columns:
        op.add_column("script_chapters", sa.Column("audit_assembly_take_id", sa.String(36), nullable=False, server_default=""))
    if "audit_narrator_fingerprint" not in columns:
        op.add_column("script_chapters", sa.Column("audit_narrator_fingerprint", sa.String(64), nullable=False, server_default=""))
    if not sa.inspect(bind).has_table("asr_audits"):
        op.create_table(
            "asr_audits",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("chapter_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("audio_file_id", sa.String(36), nullable=False, server_default=""),
            sa.Column("status", sa.String(40), nullable=False, server_default="queued"),
            sa.Column("model", sa.String(120), nullable=False, server_default=""),
            sa.Column("confirm_model", sa.String(120), nullable=False, server_default=""),
            sa.Column("judge_model", sa.String(120), nullable=False, server_default=""),
            sa.Column("transcript_json", sa.Text(), nullable=False, server_default=""),
            sa.Column("lines_json", sa.Text(), nullable=False, server_default=""),
            sa.Column("findings_json", sa.Text(), nullable=False, server_default=""),
            sa.Column("stt_cost_rub", sa.Float(), nullable=False, server_default="0"),
            sa.Column("error_message", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_asr_audits_chapter_id", "asr_audits", ["chapter_id"])
        op.create_index("ix_asr_audits_status", "asr_audits", ["status"])


def downgrade():
    bind = op.get_bind()
    if sa.inspect(bind).has_table("asr_audits"):
        op.drop_index("ix_asr_audits_status", table_name="asr_audits")
        op.drop_index("ix_asr_audits_chapter_id", table_name="asr_audits")
        op.drop_table("asr_audits")
    columns = {item["name"] for item in sa.inspect(bind).get_columns("script_chapters")}
    if "audit_narrator_fingerprint" in columns:
        op.drop_column("script_chapters", "audit_narrator_fingerprint")
    if "audit_assembly_take_id" in columns:
        op.drop_column("script_chapters", "audit_assembly_take_id")
    if "audit_assembly_fingerprint" in columns:
        op.drop_column("script_chapters", "audit_assembly_fingerprint")
