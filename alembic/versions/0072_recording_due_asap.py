"""Роль можно отслеживать без календарной даты сдачи."""
from datetime import date

from alembic import op
import sqlalchemy as sa

revision = "0072_recording_due_asap"
down_revision = "0071_ambient_pairs"
branch_labels = None
depends_on = None

# Значения, которые ставила старая общая настройка студии.
_LEGACY_DATE = "2026-12-31"
_LEGACY_MSK_DUE = "2026-12-31 20:59:59"


def _column(table, name):
    return next(c for c in sa.inspect(op.get_bind()).get_columns(table) if c["name"] == name)


def _studio_legacy_due():
    from app.services.studio_calendar import end_of_day

    zone = op.get_bind().execute(sa.text(
        "SELECT timezone FROM studio_calendar_profile WHERE id = 'default'"
    )).scalar_one_or_none()
    return end_of_day(date.fromisoformat(_LEGACY_DATE), zone or "").isoformat(sep=" ")


def upgrade():
    if not _column("role_deadlines", "due_at")["nullable"]:
        with op.batch_alter_table("role_deadlines") as batch:
            batch.alter_column("due_at", existing_type=sa.DateTime(), nullable=True)
    if _column("studio_settings", "role_deadline_date")["default"] != "''":
        with op.batch_alter_table("studio_settings") as batch:
            batch.alter_column("role_deadline_date", existing_type=sa.String(10), server_default="")
    op.execute(sa.text("UPDATE studio_settings SET role_deadline_date = '' WHERE role_deadline_date = :old")
               .bindparams(old=_LEGACY_DATE))
    # Индивидуально согласованные даты и закрытые записи нужны для истории.
    op.execute(sa.text(
        "UPDATE role_deadlines SET due_at = NULL, reminded_before_at = NULL, reminded_overdue_at = NULL "
        "WHERE kind = 'role' AND closed_at IS NULL AND COALESCE(extended_by, '') = '' "
        "AND datetime(due_at) IN (:legacy_msk, :studio_due)"
    ).bindparams(legacy_msk=_LEGACY_MSK_DUE, studio_due=_studio_legacy_due()))


def downgrade():
    op.execute(sa.text("UPDATE role_deadlines SET due_at = :old WHERE due_at IS NULL").bindparams(old=_studio_legacy_due()))
    op.execute(sa.text("UPDATE studio_settings SET role_deadline_date = :old WHERE role_deadline_date = ''")
               .bindparams(old=_LEGACY_DATE))
    if _column("role_deadlines", "due_at")["nullable"]:
        with op.batch_alter_table("role_deadlines") as batch:
            batch.alter_column("due_at", existing_type=sa.DateTime(), nullable=False)
    with op.batch_alter_table("studio_settings") as batch:
        batch.alter_column("role_deadline_date", existing_type=sa.String(10), server_default=_LEGACY_DATE)
