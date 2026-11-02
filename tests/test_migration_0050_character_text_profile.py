"""Миграция 0050: характеристика роли по тексту книги (07.10)."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0053_character_text_profile.py"


def _run(conn, step):
    spec = importlib.util.spec_from_file_location("m0050", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module, step)()


def test_upgrade_twice_then_downgrade():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("create table characters (id varchar(36) primary key, name varchar(255))"))
        conn.execute(sa.text("insert into characters values ('c1', 'Емеммо Шторм')"))
        _run(conn, "upgrade")
        _run(conn, "upgrade")
        columns = {c["name"] for c in sa.inspect(conn).get_columns("characters")}
        assert {"text_profile", "text_profile_by"} <= columns
        assert conn.execute(sa.text("select text_profile, text_profile_by from characters")).one() == ("", "")
        _run(conn, "downgrade")
        columns = {c["name"] for c in sa.inspect(conn).get_columns("characters")}
        assert not {"text_profile", "text_profile_by"} & columns
