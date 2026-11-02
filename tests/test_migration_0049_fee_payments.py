"""Миграция 0049: журнал выплат дикторам («Смета», 06.10)."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0052_fee_payments.py"


def _run(conn, step):
    spec = importlib.util.spec_from_file_location("m0049", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, step)()


def test_upgrade_twice_then_downgrade():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _run(conn, "upgrade")
        _run(conn, "upgrade")
        columns = {c["name"] for c in sa.inspect(conn).get_columns("fee_payments")}
        assert {"kind", "amount_rub", "role_key", "telegram_user_id", "note", "paid_at", "paid_by"} <= columns
        _run(conn, "downgrade")
        assert "fee_payments" not in sa.inspect(conn).get_table_names()
