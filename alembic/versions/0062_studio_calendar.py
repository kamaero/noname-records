"""Календарь выбирается для пустой новой установки, старые данные остаются UTC+3."""
import os

import sqlalchemy as sa
from alembic import op

revision = "0062_studio_calendar"
down_revision = "0061_book_language"
branch_labels = None
depends_on = None


def upgrade():
    from app.services.studio_calendar import calendar_zone

    selected = os.environ.get("STUDIO_TIMEZONE", "").strip()
    calendar_zone(selected)
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())
    if "studio_calendar_profile" not in tables:
        op.create_table("studio_calendar_profile", sa.Column("id", sa.String(36), primary_key=True),
                        sa.Column("timezone", sa.String(100), nullable=False, server_default=""))
    if conn.execute(sa.text("SELECT 1 FROM studio_calendar_profile WHERE id='default'")).first():
        return
    # Даже настройки без книг/трат означают прежнюю установку. Ни окружение,
    # ни новый релиз не имеют права переопределить её финансовый календарь.
    existing = False
    for name in tables - {"studio_calendar_profile", "alembic_version", "sqlite_sequence"}:
        table = sa.Table(name, sa.MetaData(), autoload_with=conn)
        if conn.execute(sa.select(sa.literal(1)).select_from(table).limit(1)).first():
            existing = True
            break
    conn.execute(sa.text("INSERT INTO studio_calendar_profile(id, timezone) VALUES('default', :zone)"),
                 {"zone": "" if existing else selected})


def downgrade():
    if "studio_calendar_profile" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("studio_calendar_profile")
