"""K3: feed the confirmed canon roster into char_extraction for canon authors."""
import json, sqlite3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Author, ScriptBook
from app.services import canon_seed


def _canon_file(tmp_path):
    p = str(tmp_path / "canon.sqlite")
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE canon_character (id INTEGER PRIMARY KEY, canonical TEXT, aliases_json TEXT, merge_status TEXT)")
    con.execute("INSERT INTO canon_character(canonical,aliases_json,merge_status) VALUES('Дгарнин',?, 'confirmed')", (json.dumps(["Лис","Пресвитер"]),))
    con.execute("INSERT INTO canon_character(canonical,aliases_json,merge_status) VALUES('Зодтоп','[]','confirmed')")
    con.execute("INSERT INTO canon_character(canonical,aliases_json,merge_status) VALUES('Шум','[]','accepted')")
    con.commit(); con.close()
    return p


def test_build_canon_roster_confirmed_only(tmp_path):
    roster = canon_seed.build_canon_roster(_canon_file(tmp_path))
    assert "Дгарнин (Лис, Пресвитер)" in roster
    assert "Зодтоп" in roster
    assert "Шум" not in roster  # accepted, not confirmed


def test_build_canon_roster_missing_file_is_empty(tmp_path):
    assert canon_seed.build_canon_roster(str(tmp_path / "nope.sqlite")) == ""


def test_suffix_only_for_canon_author(tmp_path):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine); Base.metadata.create_all(engine)
    cf = _canon_file(tmp_path)
    with SessionLocal() as db:
        db.add(Author(id="rud", name="Александр Белозёров", slug="belozerov"))
        db.add(Author(id="oth", name="Лев Толстой", slug="tolstoy"))
        db.commit()
        rud = ScriptBook(id="b1", title="X", source_filename="x.fb2", source_format="fb2", author_id="rud")
        oth = ScriptBook(id="b2", title="Y", source_filename="y.fb2", source_format="fb2", author_id="oth")
        none = ScriptBook(id="b3", title="Z", source_filename="z.fb2", source_format="fb2")
        assert "Дгарнин" in canon_seed.canon_roster_system_suffix(db, rud, db_path=cf)
        assert canon_seed.canon_roster_system_suffix(db, oth, db_path=cf) == ""
        assert canon_seed.canon_roster_system_suffix(db, none, db_path=cf) == ""
