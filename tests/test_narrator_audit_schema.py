"""Схема аудита обновляет существующую базу, не меняя старые данные."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models import AsrAudit, ScriptChapter


def test_audit_migration_is_idempotent_and_preserves_chapters():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0060_asr_audits.py"
    spec = importlib.util.spec_from_file_location("narrator_audit_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE script_chapters (id VARCHAR(36) PRIMARY KEY, chapter_title TEXT NOT NULL)"))
        connection.execute(sa.text("INSERT INTO script_chapters VALUES ('chapter-1', 'Пример')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
        assert connection.execute(sa.text("SELECT chapter_title FROM script_chapters WHERE id='chapter-1'")).scalar_one() == "Пример"
        chapter_columns = {column["name"] for column in sa.inspect(connection).get_columns("script_chapters")}
        assert {"audit_assembly_fingerprint", "audit_assembly_take_id", "audit_narrator_fingerprint"} <= chapter_columns
        audit_columns = {column["name"] for column in sa.inspect(connection).get_columns("asr_audits")}
        assert set(AsrAudit.__table__.columns.keys()) - {"language"} == audit_columns
        assert {"audit_assembly_fingerprint", "audit_assembly_take_id", "audit_narrator_fingerprint"} <= set(ScriptChapter.__table__.columns.keys())
