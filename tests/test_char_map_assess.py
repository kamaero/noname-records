import json
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, ScriptBook, Character, Author
from app.services.char_map_assess import assess_char_map, CharMapAssessment


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)()


def _add_char(db, book_id, name, aliases=""):
    db.add(Character(book_id=book_id, name=name, aliases=aliases))


def _canon_db(tmp_path: Path, rows) -> str:
    p = tmp_path / "canon_kb.sqlite"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE canon_character (canonical TEXT, aliases_json TEXT, merge_status TEXT, actor_name TEXT)")
    con.executemany("INSERT INTO canon_character VALUES (?,?,?,?)", rows)
    con.commit(); con.close()
    return str(p)


def test_non_canon_book_is_clean_with_canon_unavailable():
    db = _session()
    book = ScriptBook(title="X", source_filename="x.fb2", source_format="fb2", author_id="")
    db.add(book); db.flush()
    _add_char(db, book.id, "Анна", "Аня")
    _add_char(db, book.id, "Борис")
    db.commit()
    a = assess_char_map(db, book, canon_db_path="/nonexistent.sqlite")
    assert isinstance(a, CharMapAssessment)
    assert a.canon_available is False
    assert a.total == 2 and a.known == 0 and a.new == 0
    assert a.status == "clean"


def test_suspect_alias_count_makes_it_dirty():
    db = _session()
    book = ScriptBook(title="X", source_filename="x.fb2", source_format="fb2", author_id="")
    db.add(book); db.flush()
    _add_char(db, book.id, "Куйбу Дегатти", "Куйбу, Доминатор, Айза, Пригор, профессор Дегатти, Динхубия, Бес, Тень")
    db.commit()
    a = assess_char_map(db, book, canon_db_path="/nonexistent.sqlite")
    assert a.status == "dirty"
    assert a.ambiguous == 1
    assert any(r["code"] == "AMBIGUOUS_MERGE" for r in a.blocking_reasons)


def test_canon_known_vs_new_and_high_new_ratio(tmp_path):
    db = _session()
    db.add(Author(id="auth-rud", name="Белозёров", slug="belozerov"))
    book = ScriptBook(title="X", source_filename="x.fb2", source_format="fb2", author_id="auth-rud")
    db.add(book); db.flush()
    _add_char(db, book.id, "Дгарнин", "Лис")
    _add_char(db, book.id, "Новый1"); _add_char(db, book.id, "Новый2"); _add_char(db, book.id, "Новый3")
    db.commit()
    canon = _canon_db(tmp_path, [("Дгарнин", json.dumps(["Лис"], ensure_ascii=False), "confirmed", "Зотов Сергей")])
    a = assess_char_map(db, book, canon_db_path=canon)
    assert a.canon_available is True
    assert a.known == 1 and a.new == 3 and a.total == 4
    assert a.status == "dirty"  # new_ratio 0.75 > 0.40
    assert any(r["code"] == "NEW_RATIO_HIGH" for r in a.blocking_reasons)
