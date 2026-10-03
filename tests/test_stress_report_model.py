from app.db import Base, SessionLocal, engine
from app.models import ScriptBook, ScriptChapter


def test_stress_report_column_defaults_empty():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        book = ScriptBook(title="t", display_title="t", source_filename="t.txt", source_format="txt")
        db.add(book); db.flush()
        ch = ScriptChapter(book_id=book.id, chapter_index=1, chapter_title="c")
        db.add(ch); db.flush()
        assert ch.stress_report_json == ""
