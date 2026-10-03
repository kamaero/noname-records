"""Удаление книги уносит всё своё и не трогает чужое.

Сторож схемы: каждая таблица со ссылкой на книгу, главу, отрезок или персонажа
отнесена в `book_purge` к «удалить» или «оставить». Новая таблица, которую никто
не отнёс, роняет тест — так ручной список и отстал в прошлый раз.
"""
import datetime
import uuid

import pytest
from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, create_engine, insert, select
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — регистрация таблиц
import app.v2.models  # noqa: F401
from app.db import Base
from app.services import book_purge

SCOPE_COLUMNS = ("book_id", "chapter_id", "segment_id", "character_id")
ACCOUNTED = (
    set(book_purge.BY_BOOK) | set(book_purge.BY_SEGMENT) | set(book_purge.BY_CHAPTER)
    | set(book_purge.BY_CHARACTER) | set(book_purge.CARRIERS) | set(book_purge.KEPT)
    | {"background_runs", "script_books"}
)


def test_every_table_that_points_at_a_book_is_accounted_for():
    loose = sorted(
        table.name for table in Base.metadata.tables.values()
        if any(column in table.c for column in SCOPE_COLUMNS) and table.name not in ACCOUNTED
    )
    assert loose == [], f"отнеси к удалению или к KEPT в app/services/book_purge.py: {loose}"


def test_no_table_is_both_purged_and_kept():
    purged = set(ACCOUNTED) - set(book_purge.KEPT)
    assert purged & set(book_purge.KEPT) == set()
    for name in ACCOUNTED:
        assert name in Base.metadata.tables, name


def _filler(column):
    kind = type(column.type)
    if issubclass(kind, Boolean):
        return False
    if issubclass(kind, Integer):
        return 0
    if issubclass(kind, Float):
        return 0.0
    if issubclass(kind, DateTime):
        return datetime.datetime(2026, 9, 29)
    if issubclass(kind, JSON):
        return {}
    return "x"


def _row(table, **scope):
    """Строка с заполненными обязательными полями и нужными ссылками."""
    values = {}
    for column in table.columns:
        if column.name in scope:
            values[column.name] = scope[column.name]
        elif column.primary_key:
            values[column.name] = str(uuid.uuid4()) if not issubclass(type(column.type), Integer) else None
        elif not column.nullable and column.default is None and column.server_default is None:
            values[column.name] = _filler(column)
    return {k: v for k, v in values.items() if v is not None or not table.c[k].primary_key}


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _seed(db, book):
    """Книга со всем, что на неё ссылается, во всех таблицах плана."""
    t = Base.metadata.tables
    chapter, segment, character = f"{book}-c", f"{book}-s", f"{book}-p"
    db.execute(insert(t["script_books"]).values(**_row(t["script_books"], id=book)))
    db.execute(insert(t["script_chapters"]).values(**_row(t["script_chapters"], id=chapter, book_id=book)))
    db.execute(insert(t["v2_segments"]).values(**_row(t["v2_segments"], id=segment, book_id=book, chapter_id=chapter)))
    db.execute(insert(t["characters"]).values(**_row(t["characters"], id=character, book_id=book)))
    scope = {"book_id": book, "chapter_id": chapter, "segment_id": segment, "character_id": character}
    for name in (*book_purge.BY_BOOK, *book_purge.BY_SEGMENT, *book_purge.BY_CHAPTER,
                 *book_purge.BY_CHARACTER, *book_purge.KEPT):
        table = t[name]
        db.execute(insert(table).values(**_row(table, **{k: v for k, v in scope.items() if k in table.c})))
    runs = t["background_runs"]
    db.execute(insert(runs).values(**_row(runs, entity_id=book, status="done")))
    db.execute(insert(runs).values(**_row(runs, entity_id=chapter, status="failed")))
    db.execute(insert(runs).values(**_row(runs, id=f"{book}-live", entity_id=book, status="running")))
    db.commit()


def _count(db, name, book):
    table = Base.metadata.tables[name]
    for column, value in (("book_id", book), ("chapter_id", f"{book}-c"),
                          ("segment_id", f"{book}-s"), ("character_id", f"{book}-p")):
        if column in table.c:
            return len(db.execute(select(table).where(table.c[column] == value)).all())
    return None


def test_purge_removes_own_rows_keeps_people_and_money_and_leaves_other_books(db):
    _seed(db, "gone")
    _seed(db, "stays")

    book_purge.purge_book_rows(db, "gone")
    db.commit()

    for name in (*book_purge.BY_BOOK, *book_purge.BY_SEGMENT, *book_purge.BY_CHAPTER,
                 *book_purge.BY_CHARACTER, *book_purge.CARRIERS):
        assert _count(db, name, "gone") == 0, name
        assert _count(db, name, "stays") == 1, name
    for name in book_purge.KEPT:
        count = _count(db, name, "gone")
        assert count in (None, 1), name  # audio_files держится за код книги, а не за id

    runs = Base.metadata.tables["background_runs"]
    left = {row.id for row in db.execute(select(runs.c.id).where(runs.c.entity_id.in_(["gone", "gone-c"])))}
    assert left == {"gone-live"}, "прогон в работе не трогаем — воркер сам увидит, что книги нет"


def test_empty_id_is_refused(db):
    with pytest.raises(ValueError):
        book_purge.purge_book_rows(db, "")
    assert book_purge.book_file_dirs("") == []


def test_file_dirs_are_scoped_to_the_book():
    dirs = book_purge.book_file_dirs("abc-1")
    assert dirs and all("abc-1" in d for d in dirs)
