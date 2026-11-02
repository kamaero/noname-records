"""Миграция 0052: приём демо дикторов, пересланных владельцем боту (проект Б)."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0055_bot_demos.py"


def _run(conn, step):
    spec = importlib.util.spec_from_file_location("m0052", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, step)()


def test_upgrade_twice_then_downgrade():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _run(conn, "upgrade")
        _run(conn, "upgrade")
        insp = sa.inspect(conn)
        intake = {c["name"] for c in insp.get_columns("bot_demo_intakes")}
        assert {"id", "sender_key", "telegram_user_id", "tg_name", "tg_username", "state", "pending_name",
                "foreign_note", "user_id", "created_at", "updated_at"} <= intake
        item = {c["name"] for c in insp.get_columns("bot_demo_items")}
        assert {"id", "intake_id", "source_message_id", "kind", "ref", "file_name", "mime_type", "file_size",
                "text", "state", "result"} <= item
        _run(conn, "downgrade")
        tables = set(sa.inspect(conn).get_table_names())
        assert not {"bot_demo_intakes", "bot_demo_items"} & tables
