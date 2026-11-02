"""Миграция 0044: оценки 1–10 вместо 👍/👎.

Главное здесь — что выкатка не разошлёт писем: ждущие отказы по старым 👎 должны
отмениться до того, как их увидит таймер, а отправленные — остаться историей.
"""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0047_audition_scores.py"


def _load():
    spec = importlib.util.spec_from_file_location("m0044", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_schema(conn):
    conn.execute(sa.text(
        "CREATE TABLE audition_reactions (id VARCHAR(36) PRIMARY KEY, audio_file_id VARCHAR(36) NOT NULL, "
        "voter_uid VARCHAR(36) NOT NULL, voter_name VARCHAR(120) NOT NULL DEFAULT '', value INTEGER NOT NULL, "
        "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    ))
    conn.execute(sa.text(
        "CREATE TABLE audition_rejections (id VARCHAR(36) PRIMARY KEY, book_id VARCHAR(36) NOT NULL, "
        "book_code VARCHAR(40) NOT NULL DEFAULT '', role VARCHAR(120) NOT NULL, actor_name VARCHAR(120) NOT NULL, "
        "due_at DATETIME NOT NULL, status VARCHAR(12) NOT NULL DEFAULT 'pending', sent_at DATETIME, "
        "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
    ))
    conn.execute(sa.text(
        "INSERT INTO audition_reactions VALUES ('r1','a1','u1','Автор',-1,'2026-10-01','2026-10-01')"
    ))
    for rid, status in (("p", "pending"), ("s", "sending"), ("d", "sent"), ("f", "failed"), ("c", "cancelled")):
        conn.execute(sa.text(
            "INSERT INTO audition_rejections (id, book_id, role, actor_name, due_at, status, created_at, updated_at) "
            f"VALUES ('{rid}','b1','Саяху','Актёр {rid}','2026-10-01','{status}','2026-10-01','2026-10-01')"
        ))


def _upgrade(conn):
    with Operations.context(MigrationContext.configure(conn)):
        _load().upgrade()


def test_upgrade_creates_scores_drops_reactions_and_cancels_waiting_rejections():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _old_schema(conn)
        _upgrade(conn)
        tables = sa.inspect(conn).get_table_names()
        statuses = dict(conn.execute(sa.text("SELECT id, status FROM audition_rejections")).all())

    assert "audition_scores" in tables and "audition_reactions" not in tables
    assert statuses == {"p": "cancelled", "s": "cancelled", "d": "sent", "f": "failed", "c": "cancelled"}


def test_upgrade_twice_is_harmless():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _old_schema(conn)
        _upgrade(conn)
        _upgrade(conn)
        columns = {c["name"] for c in sa.inspect(conn).get_columns("audition_scores")}

    assert {"book_id", "role", "actor_name", "lane", "voter_uid", "voter_name", "score"} <= columns


MIGRATION_0045 = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0048_rejection_cause.py"


def test_0045_adds_cause_defaulting_to_approval_and_is_idempotent():
    """Существующие отказы заведены утверждением — оценки им повода не дают."""
    spec = importlib.util.spec_from_file_location("m0045", MIGRATION_0045)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _old_schema(conn)
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        causes = {row[0] for row in conn.execute(sa.text("SELECT cause FROM audition_rejections"))}
    assert causes == {"approval"}
