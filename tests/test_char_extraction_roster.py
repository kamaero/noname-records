from app.db import Base, SessionLocal, engine
from app.models import ScriptBook
from app.services import author_profile as ap
from app.services.char_extraction import _char_extraction_system_prompt


def _reset():
    Base.metadata.create_all(bind=engine)


def test_system_prompt_includes_roster_for_author_book():
    _reset()
    with SessionLocal() as db:
        a = ap.get_or_create_author(db, "rud_cx", "Белозёров")
        ap.upsert_character(db, a.id, canonical_name="Хубол", aliases=["Купец"], status="confirmed")
        book = ScriptBook(title="t", display_title="t", source_filename="t.txt", source_format="txt", author_id=a.id)
        db.add(book); db.flush()
        prompt = _char_extraction_system_prompt(db, book)
        assert "ИЗВЕСТНЫЙ КАНОН АВТОРА" in prompt and "Хубол (Купец)" in prompt


def test_system_prompt_plain_for_author_less_book():
    _reset()
    with SessionLocal() as db:
        book = ScriptBook(title="t", display_title="t", source_filename="t.txt", source_format="txt")
        db.add(book); db.flush()
        prompt = _char_extraction_system_prompt(db, book)
        assert "ИЗВЕСТНЫЙ КАНОН АВТОРА" not in prompt
