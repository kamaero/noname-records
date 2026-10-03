from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy import inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.config import settings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

connect_args = {"check_same_thread": False, "timeout": 30} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.close()


@contextmanager
def transactional(db: Session) -> Generator[Session, None, None]:
    """Context manager that commits on success and rolls back on any exception.

    Usage::

        with transactional(db) as session:
            session.query(ScriptChapter).filter(...).update(...)
            session.query(ScriptJob).filter(...).delete()
        # auto-commit here; on exception — auto-rollback, exception re-raised
    """
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


def run_migrations() -> None:
    """Bring the database schema up to head via Alembic.

    Used at app startup in place of create_all so Alembic is the schema authority.
    On a fresh DB this runs the baseline (build all tables); on the stamped
    production DB it is a no-op. Idempotent and safe to call repeatedly.
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")


def apply_additive_migrations() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    migrations = {
        "audio_files": {
            "canonical_filename": "ALTER TABLE audio_files ADD COLUMN canonical_filename VARCHAR(255) NOT NULL DEFAULT ''",
            "book_code": "ALTER TABLE audio_files ADD COLUMN book_code VARCHAR(40) NOT NULL DEFAULT ''",
            "line_index": "ALTER TABLE audio_files ADD COLUMN line_index INTEGER",
        },
        "script_books": {
            "display_title": "ALTER TABLE script_books ADD COLUMN display_title VARCHAR(255) NOT NULL DEFAULT ''",
            "author_label": "ALTER TABLE script_books ADD COLUMN author_label VARCHAR(120) NOT NULL DEFAULT ''",
            "llm_provider": "ALTER TABLE script_books ADD COLUMN llm_provider VARCHAR(40) NOT NULL DEFAULT 'openai'",
            "llm_model": "ALTER TABLE script_books ADD COLUMN llm_model VARCHAR(120) NOT NULL DEFAULT ''",
            "pipeline_mode": "ALTER TABLE script_books ADD COLUMN pipeline_mode VARCHAR(40) NOT NULL DEFAULT 'standard'",
            "validation_profile": "ALTER TABLE script_books ADD COLUMN validation_profile VARCHAR(40) NOT NULL DEFAULT 'operator_only'",
            "genre_guidelines": "ALTER TABLE script_books ADD COLUMN genre_guidelines TEXT NOT NULL DEFAULT ''",
            "book_annotation": "ALTER TABLE script_books ADD COLUMN book_annotation TEXT NOT NULL DEFAULT ''",
            "narrative_owner_name": "ALTER TABLE script_books ADD COLUMN narrative_owner_name VARCHAR(120) NOT NULL DEFAULT ''",
            "narrative_owner_confidence": "ALTER TABLE script_books ADD COLUMN narrative_owner_confidence FLOAT NOT NULL DEFAULT 0",
            "narrative_owner_reason": "ALTER TABLE script_books ADD COLUMN narrative_owner_reason VARCHAR(255) NOT NULL DEFAULT ''",
            "domain_lexicon": "ALTER TABLE script_books ADD COLUMN domain_lexicon TEXT NOT NULL DEFAULT ''",
            "pronunciation_notes": "ALTER TABLE script_books ADD COLUMN pronunciation_notes TEXT NOT NULL DEFAULT ''",
            "stop_requested": "ALTER TABLE script_books ADD COLUMN stop_requested VARCHAR(5) NOT NULL DEFAULT 'false'",
            "needs_investigation": "ALTER TABLE script_books ADD COLUMN needs_investigation VARCHAR(5) NOT NULL DEFAULT 'false'",
            "current_pipeline_run_id": "ALTER TABLE script_books ADD COLUMN current_pipeline_run_id VARCHAR(36) NOT NULL DEFAULT ''",
            "previous_pipeline_run_id": "ALTER TABLE script_books ADD COLUMN previous_pipeline_run_id VARCHAR(36) NOT NULL DEFAULT ''",
            "created_by_user_id": "ALTER TABLE script_books ADD COLUMN created_by_user_id VARCHAR(36) NOT NULL DEFAULT ''",
            "created_by_name": "ALTER TABLE script_books ADD COLUMN created_by_name VARCHAR(120) NOT NULL DEFAULT ''",
        },
        "script_chapters": {
        },
        "script_jobs": {
            "stage": "ALTER TABLE script_jobs ADD COLUMN stage VARCHAR(20) NOT NULL DEFAULT 'draft'",
            "pipeline_run_id": "ALTER TABLE script_jobs ADD COLUMN pipeline_run_id VARCHAR(36) NOT NULL DEFAULT ''",
            "error_code": "ALTER TABLE script_jobs ADD COLUMN error_code VARCHAR(80) NOT NULL DEFAULT ''",
            "error_details_json": "ALTER TABLE script_jobs ADD COLUMN error_details_json TEXT NOT NULL DEFAULT ''",
        },
        "script_logs": {
            "pipeline_run_id": "ALTER TABLE script_logs ADD COLUMN pipeline_run_id VARCHAR(36) NOT NULL DEFAULT ''",
            "event_code": "ALTER TABLE script_logs ADD COLUMN event_code VARCHAR(80) NOT NULL DEFAULT ''",
            "event_details_json": "ALTER TABLE script_logs ADD COLUMN event_details_json TEXT NOT NULL DEFAULT ''",
        },
        "characters": {
            "char_map_id": "ALTER TABLE characters ADD COLUMN char_map_id VARCHAR(36) NOT NULL DEFAULT ''",
            "aliases": "ALTER TABLE characters ADD COLUMN aliases TEXT NOT NULL DEFAULT ''",
            "appears_in": "ALTER TABLE characters ADD COLUMN appears_in TEXT NOT NULL DEFAULT ''",
            "character_color": "ALTER TABLE characters ADD COLUMN character_color VARCHAR(20) NOT NULL DEFAULT ''",
            "character_text_color": "ALTER TABLE characters ADD COLUMN character_text_color VARCHAR(20) NOT NULL DEFAULT ''",
            "character_font_weight": "ALTER TABLE characters ADD COLUMN character_font_weight VARCHAR(20) NOT NULL DEFAULT ''",
            "character_font_style": "ALTER TABLE characters ADD COLUMN character_font_style VARCHAR(20) NOT NULL DEFAULT ''",
            "narrator_role": "ALTER TABLE characters ADD COLUMN narrator_role VARCHAR(120) NOT NULL DEFAULT ''",
        },
        "book_budget": {
            "narrator_actor_name": "ALTER TABLE book_budget ADD COLUMN narrator_actor_name VARCHAR(120) NOT NULL DEFAULT ''",
        },
        "asr_jobs": {
            "decision": "ALTER TABLE asr_jobs ADD COLUMN decision VARCHAR(40) NOT NULL DEFAULT ''",
            "expected_role": "ALTER TABLE asr_jobs ADD COLUMN expected_role VARCHAR(120) NOT NULL DEFAULT ''",
            "detected_role": "ALTER TABLE asr_jobs ADD COLUMN detected_role VARCHAR(120) NOT NULL DEFAULT ''",
            "expected_chapter_index": "ALTER TABLE asr_jobs ADD COLUMN expected_chapter_index INTEGER NOT NULL DEFAULT 0",
            "detected_chapter_index": "ALTER TABLE asr_jobs ADD COLUMN detected_chapter_index INTEGER NOT NULL DEFAULT 0",
            "similarity": "ALTER TABLE asr_jobs ADD COLUMN similarity FLOAT NOT NULL DEFAULT 0",
            "coverage": "ALTER TABLE asr_jobs ADD COLUMN coverage FLOAT NOT NULL DEFAULT 0",
            "timing_fit": "ALTER TABLE asr_jobs ADD COLUMN timing_fit FLOAT NOT NULL DEFAULT 0",
            "confidence_score": "ALTER TABLE asr_jobs ADD COLUMN confidence_score FLOAT NOT NULL DEFAULT 0",
            "review_reason": "ALTER TABLE asr_jobs ADD COLUMN review_reason VARCHAR(255) NOT NULL DEFAULT ''",
        },
        "telegram_auth_accounts": {
            "access_scope": "ALTER TABLE telegram_auth_accounts ADD COLUMN access_scope VARCHAR(20) NOT NULL DEFAULT 'full'",
        },
        "background_runs": {},
        "pipeline_runs": {},
        "pipeline_events": {},
        "operator_interventions": {
            "chapter_id": "ALTER TABLE operator_interventions ADD COLUMN chapter_id VARCHAR(36) NOT NULL DEFAULT ''",
        },
    }

    with engine.begin() as conn:
        for table, columns in migrations.items():
            if table not in existing_tables:
                continue
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            for name, sql in columns.items():
                if name not in existing_columns:
                    conn.execute(text(sql))
        if "characters" in existing_tables:
            conn.execute(text("UPDATE characters SET char_map_id = id WHERE char_map_id IS NULL OR char_map_id = ''"))

        # Indexes (idempotent — IF NOT EXISTS)
        index_sqls = [
            "CREATE INDEX IF NOT EXISTS ix_audio_files_book_code ON audio_files (book_code)",
            "CREATE INDEX IF NOT EXISTS ix_audio_files_canonical_filename ON audio_files (canonical_filename)",
            "CREATE INDEX IF NOT EXISTS ix_asr_jobs_audio_file_id ON asr_jobs (audio_file_id)",
            "CREATE INDEX IF NOT EXISTS ix_asr_jobs_chapter_id ON asr_jobs (chapter_id)",
            "CREATE INDEX IF NOT EXISTS ix_asr_jobs_status ON asr_jobs (status)",
            "CREATE INDEX IF NOT EXISTS ix_script_chapters_book_id ON script_chapters (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_script_chapters_book_status ON script_chapters (book_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_script_chapters_book_index ON script_chapters (book_id, chapter_index)",
            "CREATE INDEX IF NOT EXISTS ix_script_jobs_book_id ON script_jobs (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_script_jobs_chapter_id ON script_jobs (chapter_id)",
            "CREATE INDEX IF NOT EXISTS ix_script_jobs_book_stage_status ON script_jobs (book_id, stage, status)",
            "CREATE INDEX IF NOT EXISTS ix_script_logs_book_id ON script_logs (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_script_logs_created_at ON script_logs (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_user_roles_user_id ON user_roles (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_characters_book_id ON characters (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_characters_book_name ON characters (book_id, name)",
            "CREATE INDEX IF NOT EXISTS ix_characters_char_map_id ON characters (char_map_id)",
            "CREATE INDEX IF NOT EXISTS ix_char_budget_snapshot_book_id ON character_budget_snapshot (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_char_budget_snapshot_character_id ON character_budget_snapshot (character_id)",
            "CREATE INDEX IF NOT EXISTS ix_audit_log_created_at ON audit_log (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_audit_log_entity ON audit_log (entity_type, entity_id)",
            "CREATE INDEX IF NOT EXISTS ix_audit_log_user_id ON audit_log (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_llm_usage_logs_book_id ON llm_usage_logs (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_llm_usage_logs_chapter_id ON llm_usage_logs (chapter_id)",
            "CREATE INDEX IF NOT EXISTS ix_llm_usage_logs_created_at ON llm_usage_logs (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_background_runs_run_key ON background_runs (run_key)",
            "CREATE INDEX IF NOT EXISTS ix_background_runs_entity_id ON background_runs (entity_id)",
            "CREATE INDEX IF NOT EXISTS ix_background_runs_status ON background_runs (status)",
            "CREATE INDEX IF NOT EXISTS ix_pipeline_runs_book_id ON pipeline_runs (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_pipeline_runs_status ON pipeline_runs (status)",
            "CREATE INDEX IF NOT EXISTS ix_pipeline_runs_started_at ON pipeline_runs (started_at)",
            "CREATE INDEX IF NOT EXISTS ix_pipeline_events_run_id ON pipeline_events (pipeline_run_id)",
            "CREATE INDEX IF NOT EXISTS ix_pipeline_events_book_id ON pipeline_events (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_pipeline_events_created_at ON pipeline_events (created_at)",
            "CREATE INDEX IF NOT EXISTS ix_operator_interventions_book_id ON operator_interventions (book_id)",
            "CREATE INDEX IF NOT EXISTS ix_operator_interventions_chapter_id ON operator_interventions (chapter_id)",
            "CREATE INDEX IF NOT EXISTS ix_operator_interventions_run_id ON operator_interventions (pipeline_run_id)",
            "CREATE INDEX IF NOT EXISTS ix_operator_interventions_created_at ON operator_interventions (created_at)",
        ]
        for sql in index_sqls:
            # Extract table name from SQL to skip if table doesn't exist yet
            table = sql.split(" ON ")[1].split(" ")[0]
            if table in existing_tables:
                conn.execute(text(sql))
