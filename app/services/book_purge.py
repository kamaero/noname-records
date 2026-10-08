"""Удаление книги: какие строки уходят вместе с ней, а какие переживают её.

Список был ручным и отстал от схемы: конвейер v2, консилиум, звук и эмбиент
появились после него, и их строки оставались сиротами на удалённой книге. Теперь
каждая таблица, у которой есть ссылка на книгу, главу, отрезок или персонажа,
обязана стоять в одном из списков ниже — `tests/test_book_purge.py` падает на новой
таблице, которую никто не отнёс ни к «удалить», ни к «оставить».

Остаётся то, что принадлежит не разметке, а людям и деньгам:

* записи актёров (`audio_files`) держатся за код книги, а не за её id, и не
  удалялись никогда — это чужая работа, а перезалитая книга с тем же названием
  находит их снова;
* их распознавание (`asr_jobs`) оплачено и переигрывается без новых трат;
* журнал трат на модели (`llm_usage_logs`, `spend_entries`) — это деньги, по нему сверяют счёт.
"""
from __future__ import annotations

from sqlalchemy import delete, or_, select

from app.db import Base

#: строки книги — по колонке book_id
BY_BOOK = (
    "ambient_tracks",
    "audition_rejections",
    "book_budget",
    "book_illustrations",
    "character_budget_snapshot",
    "consilium_findings",
    "dictor_assignments",
    "operator_interventions",
    "pipeline_events",
    "pipeline_runs",
    "role_deadlines",
    "role_pair_notes",
    "script_jobs",
    "script_logs",
    "sound_markers",
    "sound_place_pairs",
    "sound_places",
    "v2_runs",
    "v2_stress_skips",
)
#: строки отрезков книги — по segment_id
BY_SEGMENT = ("v2_attributions", "v2_stress_marks")
#: строки глав книги — по chapter_id
BY_CHAPTER: tuple[str, ...] = ()
#: строки персонажей книги — по character_id
BY_CHARACTER = ("role_votes",)
#: сами носители ссылок — последними, после всего, что на них ссылается
CARRIERS = ("v2_segments", "characters", "script_chapters")

#: переживают книгу — причины в описании модуля
KEPT = ("asr_jobs", "audio_files", "bot_broadcasts", "llm_usage_logs", "spend_entries", "spend_holds")

#: прогон в работе — его строку не трогаем: воркер сам увидит, что книги нет
ACTIVE_RUN_STATUSES = ("queued", "running")


def _table(name: str):
    # Таблицы v2 регистрируются импортом своего модуля: процесс, который его ещё не
    # сделал, не нашёл бы их в схеме.
    import app.models  # noqa: F401
    import app.v2.models  # noqa: F401

    return Base.metadata.tables[name]


def purge_book_rows(db, book_id: str) -> dict[str, int]:
    """Удалить всё, что принадлежит книге, кроме самой строки книги. Коммитит вызывающий."""
    book_id = str(book_id or "").strip()
    if not book_id:
        raise ValueError("book_id_required")
    chapters = _table("script_chapters")
    segments = _table("v2_segments")
    characters = _table("characters")
    chapter_ids = select(chapters.c.id).where(chapters.c.book_id == book_id)
    segment_ids = select(segments.c.id).where(segments.c.book_id == book_id)
    character_ids = select(characters.c.id).where(characters.c.book_id == book_id)

    removed: dict[str, int] = {}

    def run(name: str, condition) -> None:
        removed[name] = int(db.execute(delete(_table(name)).where(condition)).rowcount or 0)

    for name in BY_SEGMENT:
        run(name, _table(name).c.segment_id.in_(segment_ids))
    for name in BY_CHAPTER:
        run(name, _table(name).c.chapter_id.in_(chapter_ids))
    for name in BY_CHARACTER:
        run(name, _table(name).c.character_id.in_(character_ids))
    for name in BY_BOOK:
        run(name, _table(name).c.book_id == book_id)

    runs = _table("background_runs")
    run("background_runs", (
        or_(runs.c.entity_id == book_id, runs.c.entity_id.in_(chapter_ids))
        & runs.c.status.notin_(ACTIVE_RUN_STATUSES)
    ))

    for name in CARRIERS:
        run(name, _table(name).c.book_id == book_id)
    return removed


def book_file_dirs(book_id: str) -> list[str]:
    """Каталоги книги на диске: исходник, память персонажей, отчёты, иллюстрации."""
    from app.paths import data_path
    from app.services.book_images import image_dir

    book_id = str(book_id or "").strip()
    if not book_id:
        # Пустой id дал бы каталоги-родители: `data/book_sources/` целиком.
        return []
    return [
        str(data_path("book_sources", book_id)),
        str(data_path("char_memory", book_id)),
        str(data_path("book_reports", book_id)),
        image_dir(book_id),
    ]
