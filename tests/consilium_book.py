"""Маленькая книга для тестов движка: две главы, каст, разметка версии 1."""
import uuid

from app.models import Character, ScriptBook, ScriptChapter
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment

BOOK = "b1"
CHAPTERS = {
    1: [("— Идём, — сказал Гамук.", [(0, 7, "Гамук"), (7, 23, "Рассказчик")]),
        ("— Куда?", [(0, 7, "Тупуг")]),
        ("Они ушли.", [(0, 9, "Рассказчик")])],
    2: [("— Стой!", [(0, 7, "Тупуг")]),
        ("Тишина.", [(0, 7, "Рассказчик")])],
}


def build_book(db, chapters=CHAPTERS, status="author_review"):
    db.add(ScriptBook(id=BOOK, title="Книга", source_filename="k.txt", source_format="txt",
                      status=status, created_at=utcnow_naive()))
    for name, race in (("Гамук", "демон"), ("Тупуг", "демон")):
        db.add(Character(id=str(uuid.uuid4()), book_id=BOOK, name=name, aliases="", race=race,
                         appears_in="", created_at=utcnow_naive()))
    for index, paragraphs in chapters.items():
        chapter_id = f"c{index}"
        db.add(ScriptChapter(id=chapter_id, book_id=BOOK, chapter_index=index, chapter_title=str(index)))
        for ordinal, (text, spans) in enumerate(paragraphs):
            segment_id = f"{chapter_id}:{ordinal:05d}"
            db.add(V2Segment(id=segment_id, book_id=BOOK, chapter_id=chapter_id, ordinal=ordinal,
                             kind="paragraph", text=text))
            for start, end, speaker in spans:
                db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, version=1,
                                     speaker=speaker, span_start=start, span_end=end, source="llm",
                                     confidence=0.9, created_at=utcnow_naive()))
    db.commit()
