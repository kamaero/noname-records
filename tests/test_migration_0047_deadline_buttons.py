"""Миграция 0047: отметки кнопок бота в сроке (пакет B). Повторный прогон ничего не ломает."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0050_deadline_buttons.py"
NEW = {"accepted_at", "declined_at", "extend_request_days", "extend_request_at"}


def _load():
    spec = importlib.util.spec_from_file_location("m0047", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step):
    with Operations.context(MigrationContext.configure(conn)):
        getattr(_load(), step)()


def test_upgrade_adds_columns_twice_safely_and_downgrade_removes_them():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE role_deadlines (id VARCHAR(36) PRIMARY KEY, character_id VARCHAR(36) NOT NULL, "
            "book_id VARCHAR(36) NOT NULL, actor_name VARCHAR(120) NOT NULL, kind VARCHAR(10) NOT NULL, "
            "due_at DATETIME NOT NULL, created_at DATETIME NOT NULL)"))
        _run(conn, "upgrade")
        _run(conn, "upgrade")
        assert NEW <= {c["name"] for c in sa.inspect(conn).get_columns("role_deadlines")}
        _run(conn, "downgrade")
        assert not NEW & {c["name"] for c in sa.inspect(conn).get_columns("role_deadlines")}
