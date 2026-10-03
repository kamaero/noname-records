"""удалить таблицы и колонки конвейера v1

Код v1 удалён 2026-09-29 (docs/superpowers/plans/2026-09-29-v1-removal.md); его таблицы
и колонки в базе больше никто не читает и не пишет:

* таблицы `script_chapter_artifacts` (черновики и финалы глав v1 — все строки от уже
  удалённой книги), `chapter_reviews`, `recording_progress`, `daw_export_jobs`,
  `retention_jobs` (последние четыре пусты);
* колонки глав с разметкой v1, оценками качества и указателями на артефакты;
* колонки книг с моделями черновика, финала и полировки v1 и флагом «Памяти персонажей».

Перед выкаткой база сохранена (`backups/noname.db.pre-v1-drop-*`), артефакты выгружены
в `data/stress_eval/script_chapter_artifacts_20260929.jsonl.gz`, эталон ударений v1 — в
`data/stress_eval/polumrak_v1_fountain.json`.

Каждый шаг проверяет наличие: миграции применяются сами при старте службы, и на свежей
базе (тесты) этих таблиц и колонок может не быть вовсе. Колонки удаляются родным
`ALTER TABLE … DROP COLUMN` (SQLite ≥ 3.35): ни одна из них не входит в индекс.

Откат возвращает схему, но не данные — они только в бэкапе.

Revision ID: 0038_drop_v1_tables
Revises: 0037_audition_reactions
Create Date: 2026-09-29
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0038_drop_v1_tables"
down_revision: Union[str, None] = "0037_audition_reactions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("script_chapter_artifacts", "chapter_reviews", "recording_progress", "daw_export_jobs", "retention_jobs")

#: колонка → её прежнее объявление (для отката схемы)
CHAPTER_COLUMNS = {
    "draft_fountain_text": "TEXT NOT NULL DEFAULT ''",
    "fountain_text": "TEXT NOT NULL DEFAULT ''",
    "segments_json": "TEXT NOT NULL DEFAULT ''",
    "llm_json": "TEXT NOT NULL DEFAULT ''",
    "confidence_json": "TEXT NOT NULL DEFAULT ''",
    "user_hint": "TEXT NOT NULL DEFAULT ''",
    "quality_score": "INTEGER",
    "quality_grade": "VARCHAR(2) NOT NULL DEFAULT ''",
    "quality_issues": "VARCHAR(512) NOT NULL DEFAULT ''",
    "artifact_char_extraction_id": "VARCHAR(36) NOT NULL DEFAULT ''",
    "artifact_draft_id": "VARCHAR(36) NOT NULL DEFAULT ''",
    "artifact_final_id": "VARCHAR(36) NOT NULL DEFAULT ''",
    "artifact_unsure_pass_id": "VARCHAR(36) NOT NULL DEFAULT ''",
    "artifact_polishing_id": "VARCHAR(36) NOT NULL DEFAULT ''",
    "artifact_manual_override_id": "VARCHAR(36) NOT NULL DEFAULT ''",
    "effective_artifact_id": "VARCHAR(36) NOT NULL DEFAULT ''",
}
BOOK_COLUMNS = {
    "draft_provider": "VARCHAR(40) NOT NULL DEFAULT 'deepseek'",
    "draft_model": "VARCHAR(120) NOT NULL DEFAULT 'deepseek-chat'",
    "final_provider": "VARCHAR(40) NOT NULL DEFAULT 'deepseek'",
    "final_model": "VARCHAR(120) NOT NULL DEFAULT 'deepseek-chat'",
    "polishing_provider": "VARCHAR(40) NOT NULL DEFAULT 'routerai.ru'",
    "polishing_model": "VARCHAR(120) NOT NULL DEFAULT ''",
    "char_memory_needs_rerun": "VARCHAR(5) NOT NULL DEFAULT 'false'",
}


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    present = _tables()
    if "background_runs" in present:
        # Цикл сторожа хранения удалён вместе с v1; его строка иначе висела бы «running»
        # навсегда и выглядела бы живым фоновым потоком.
        op.execute(
            "UPDATE background_runs SET status = 'stopped' "
            "WHERE job_kind = 'retention_loop' AND status IN ('queued', 'running')"
        )
    for table in TABLES:
        if table in present:
            op.drop_table(table)
    for table, columns in (("script_chapters", CHAPTER_COLUMNS), ("script_books", BOOK_COLUMNS)):
        if table not in present:
            continue
        existing = _columns(table)
        for name in columns:
            if name in existing:
                op.execute(f"ALTER TABLE {table} DROP COLUMN {name}")


def downgrade() -> None:
    present = _tables()
    for table, columns in (("script_chapters", CHAPTER_COLUMNS), ("script_books", BOOK_COLUMNS)):
        if table not in present:
            continue
        existing = _columns(table)
        for name, declaration in columns.items():
            if name not in existing:
                op.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    # Таблицы v1 не восстанавливаются: их модели удалены, данные — в бэкапе.
