"""Старый срок снимается во всех установках независимо от календаря студии."""

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine


def test_only_open_roles_with_the_old_default_lose_their_date():
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "alembic/versions/0072_recording_due_asap.py"
    spec = spec_from_file_location("rev0072", path)
    migration = module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("""
            CREATE TABLE role_deadlines (id TEXT PRIMARY KEY, kind TEXT, due_at DATETIME NOT NULL,
              closed_at DATETIME, extended_by TEXT, reminded_before_at DATETIME, reminded_overdue_at DATETIME)
        """)
        conn.exec_driver_sql("""
            CREATE TABLE studio_settings
              (id TEXT PRIMARY KEY, role_deadline_date VARCHAR(10) NOT NULL DEFAULT '2026-12-31')
        """)
        conn.exec_driver_sql("INSERT INTO studio_settings (id) VALUES ('default')")
        conn.exec_driver_sql("CREATE TABLE studio_calendar_profile (id TEXT PRIMARY KEY, timezone TEXT)")
        conn.exec_driver_sql("INSERT INTO studio_calendar_profile VALUES ('default', 'America/New_York')")
        local = "2027-01-01 04:59:59"
        msk = "2026-12-31 20:59:59"
        custom = "2027-02-01 04:59:59"
        conn.exec_driver_sql(
            "INSERT INTO role_deadlines (id, kind, due_at, closed_at, extended_by) VALUES (?, ?, ?, ?, ?)",
            [("local", "role", local, None, ""), ("msk", "role", msk, None, ""),
             ("audition", "audition", local, None, ""), ("extended", "role", local, None, "admin"),
             ("closed", "role", msk, msk, ""), ("custom", "role", custom, None, "")],
        )
        with Operations.context(MigrationContext.configure(conn)):
            migration.upgrade()
            migration.upgrade()
        result = dict(conn.exec_driver_sql("SELECT id, due_at FROM role_deadlines").all())
        assert result == {"local": None, "msk": None, "audition": local, "extended": local,
                          "closed": msk, "custom": custom}
        assert conn.exec_driver_sql("SELECT role_deadline_date FROM studio_settings").scalar_one() == ""
