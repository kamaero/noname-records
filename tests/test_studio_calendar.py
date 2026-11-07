"""Новый календарь учитывает DST, а миграция не сдвигает прежние деньги и сроки."""
import importlib.util
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import RoleDeadline, SpendEntry, StudioCalendarProfile, StudioSettings
from app.services import role_deadlines, spend
from app.services.studio_calendar import calendar_zone, end_of_day, local_time, studio_timezone


def _migration(conn):
    source = Path(__file__).resolve().parents[1] / "alembic/versions/0062_studio_calendar.py"
    spec = importlib.util.spec_from_file_location("calendar_migration", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn)):
        module.upgrade()


def test_fresh_migration_freezes_explicit_timezone_and_ignores_later_environment(monkeypatch):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        monkeypatch.setenv("STUDIO_TIMEZONE", "America/New_York")
        _migration(conn)
        monkeypatch.setenv("STUDIO_TIMEZONE", "Europe/London")
        _migration(conn)
        assert conn.execute(sa.text("SELECT timezone FROM studio_calendar_profile")).scalar_one() == "America/New_York"


@pytest.mark.parametrize("table", ["script_books", "role_deadlines", "spend_entries", "studio_settings", "users"])
def test_migration_preserves_every_nonempty_legacy_database(monkeypatch, table):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        original = sa.Table(table, sa.MetaData(), sa.Column("id", sa.String, primary_key=True),
                            sa.Column("value", sa.String))
        original.create(conn)
        payload = '2026-10-31T21:30:00; due=2026-12-31T20:59:59; RUB=123.45; original text'
        conn.execute(original.insert().values(id="original", value=payload))
        monkeypatch.setenv("STUDIO_TIMEZONE", "America/New_York")
        _migration(conn)
        _migration(conn)
        assert conn.execute(sa.text("SELECT timezone FROM studio_calendar_profile")).scalar_one() == ""
        assert conn.execute(sa.select(original)).all() == [("original", payload)]


def test_unset_calendar_preserves_legacy_and_invalid_zone_aborts_before_schema_change(monkeypatch):
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        monkeypatch.setenv("STUDIO_TIMEZONE", "Unknown/Nowhere")
        with pytest.raises(ValueError, match="Invalid studio IANA timezone"):
            _migration(conn)
        assert "studio_calendar_profile" not in sa.inspect(conn).get_table_names()
        monkeypatch.delenv("STUDIO_TIMEZONE")
        _migration(conn)
        assert conn.execute(sa.text("SELECT timezone FROM studio_calendar_profile")).scalar_one() == ""


@pytest.mark.parametrize(("day", "expected"), [
    (date(2026, 3, 8), datetime(2026, 3, 9, 3, 59, 59)),
    (date(2026, 11, 1), datetime(2026, 11, 2, 4, 59, 59)),
])
def test_dst_deadline_uses_the_actual_end_of_local_day(day, expected):
    assert end_of_day(day, "America/New_York") == expected
    assert end_of_day(day) == datetime.combine(day, datetime.max.time()).replace(microsecond=0) - timedelta(hours=3)


def test_repeated_hour_has_distinct_labels_and_nonexistent_day_is_not_guessed():
    first = role_deadlines.format_due(datetime(2026, 11, 1, 5, 30), kind="time", timezone="America/New_York")
    second = role_deadlines.format_due(datetime(2026, 11, 1, 6, 30), kind="time", timezone="America/New_York")
    assert "01:30" in first and "EDT" in first
    assert "01:30" in second and "EST" in second
    with pytest.raises(ValueError, match="does not exist"):
        end_of_day(date(2011, 12, 30), "Pacific/Apia")


@pytest.mark.parametrize(("month", "start", "end"), [
    ("2026-03", datetime(2026, 3, 1, 5), datetime(2026, 4, 1, 4)),
    ("2026-11", datetime(2026, 11, 1, 4), datetime(2026, 12, 1, 5)),
])
def test_spending_month_boundaries_follow_dst(month, start, end):
    assert spend.month_bounds(month, timezone="America/New_York") == (start, end)
    assert spend.month_key(end - timedelta(seconds=1), timezone="America/New_York") == month
    assert spend.month_key(end, timezone="America/New_York") != month


def test_legacy_month_boundaries_are_unchanged():
    assert spend.month_key(datetime(2026, 10, 31, 20, 30)) == "2026-10"
    assert spend.month_key(datetime(2026, 10, 31, 21, 10)) == "2026-11"
    assert spend.month_bounds("2026-11") == (datetime(2026, 10, 31, 21), datetime(2026, 11, 30, 21))


def test_saved_timezone_controls_spending_and_new_deadlines_without_rewriting_existing_rows(monkeypatch):
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(spend, "drain_spool", lambda: 0)
    with sessionmaker(bind=engine)() as db:
        db.add(StudioCalendarProfile(id="default", timezone="America/New_York"))
        db.add(StudioSettings(id="default", role_deadline_date="2026-03-08"))
        old_due = datetime(2026, 12, 31, 20, 59, 59)
        db.add(RoleDeadline(id="old", character_id="role", book_id="book", actor_name="Test Actor", kind="role", due_at=old_due))
        for number, hour in enumerate((3, 4)):
            db.add(SpendEntry(created_at=datetime(2026, 4, 1, hour, 30), step="asr", provider="test", model="test",
                              unit="seconds", rub=7 + number * 4, price_known=True))
        db.commit()
        assert role_deadlines.role_due_date(db) == datetime(2026, 3, 9, 3, 59, 59)
        assert role_deadlines.due_for(db, "audition", now=datetime(2026, 3, 7, 12)) == datetime(2026, 3, 9, 12)
        assert spend.month_summary(db, "2026-03")["total_rub"] == 7
        assert spend.month_summary(db, "2026-04")["total_rub"] == 11
        assert db.get(RoleDeadline, "old").due_at == old_due
        assert [row.rub for row in db.query(SpendEntry).order_by(SpendEntry.created_at)] == [7, 11]
        assert studio_timezone(db) == "America/New_York"


def test_invalid_saved_timezone_is_not_silently_interpreted():
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(StudioCalendarProfile(id="default", timezone="Unknown/Nowhere"))
        db.commit()
        with pytest.raises(ValueError, match="Invalid studio IANA timezone"):
            studio_timezone(db)


def test_full_fresh_migration_chain_uses_selected_calendar(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = """
from alembic import command
from alembic.config import Config
from app.db import engine
from sqlalchemy import text
command.upgrade(Config('alembic.ini'), '0062_studio_calendar')
with engine.connect() as db:
    assert db.execute(text('SELECT timezone FROM studio_calendar_profile')).scalar_one() == 'America/New_York'
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=30,
                            env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path / 'fresh.db'}",
                                 "STUDIO_TIMEZONE": "America/New_York", "PRODUCTION": "false"})
    assert result.returncode == 0, result.stderr


def test_typed_character_named_narrator_receives_a_deadline():
    from app.models import Character, ScriptBook, ScriptChapter
    from app.services.shared_runtime import NARRATOR_DISPLAY_NAME
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(ScriptBook(id="book", title="Silver Keys", source_filename="sample.txt", source_format="txt",
                          role_identity_mode="typed_v1", language="en"))
        db.add(ScriptChapter(id="chapter", book_id="book", chapter_index=1, chapter_title="Chapter 1", status="published"))
        db.add(Character(id="character", book_id="book", name="Narrator", actor_name="Test Actor", appears_in="1"))
        db.add(Character(id="system", book_id="book", name=NARRATOR_DISPLAY_NAME, actor_name="System Reader"))
        db.commit()
        assert role_deadlines.sync_book(db, "book", now=datetime(2026, 1, 1))["opened"] == 1
        assert db.query(RoleDeadline).one().character_id == "character"


@pytest.mark.parametrize("day", [date(2026, 3, 7), date(2026, 10, 31)])
def test_role_extension_in_a_new_calendar_keeps_end_of_day_across_dst(day):
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(StudioCalendarProfile(id="default", timezone="America/New_York"))
        old_due = end_of_day(day, "America/New_York")
        db.add(RoleDeadline(id="role", character_id="character", book_id="book", actor_name="Test Actor",
                            kind="role", due_at=old_due))
        db.commit()
        updated = role_deadlines.extend(db, "role", days=1, now=old_due - timedelta(hours=1))
        assert updated.due_at == end_of_day(day + timedelta(days=1), "America/New_York")


def test_calendar_api_requires_auth_and_exposes_exact_saved_zone(monkeypatch):
    from fastapi.testclient import TestClient
    from app.auth import session_serializer
    from app.main import app
    engine = sa.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=sa.pool.StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr("app.api.studio_calendar.SessionLocal", sessions)
    client = TestClient(app)
    assert client.get("/api/studio/calendar").status_code == 401
    client.cookies.set("session", session_serializer.dumps({"uid": "reader", "sub": "Test Reader", "roles": ["dictor"]}))
    legacy = client.get("/api/studio/calendar").json()
    assert legacy["mode"] == "legacy" and legacy["deadline_timezone"] == "Europe/Moscow"
    assert legacy["display_timezone"] == "Asia/Yekaterinburg"
    with sessions() as db:
        db.add(StudioCalendarProfile(id="default", timezone="America/New_York"))
        db.commit()
    profile = client.get("/api/studio/calendar").json()
    assert profile["mode"] == "iana"
    assert {profile[key] for key in ("timezone", "display_timezone", "deadline_timezone", "spending_timezone")} == {"America/New_York"}
