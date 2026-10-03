"""What an actor's screen needs to know before they can start.

The endpoint used to answer with a chapter list and a log of recent uploads, and
nothing else — although the context behind it already computed the cast, the actor's
own roles, the line counts and the author's colours. So the screen derived the role
list from files that had already been uploaded, which is empty for every actor on
their first day, and fell back to a free-text field where a character's name had to
be typed from memory.
"""
import os
import sys
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.services.recording_workspace import build_recording_workspace_payload
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-kp"

# Кто говорит в какой главе — разметкой v2: Химера дважды и Дгарнин раз в первой,
# Дгарнин дважды во второй.
SPEAKERS = {"ch1": ("Химера", "Дгарнин", "Химера"), "ch2": ("Дгарнин", "Дгарнин")}


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed(db):
    db.add(ScriptBook(
        id=BOOK, title="Крылья полумрака", display_title="Крылья полумрака",
        source_filename="x.docx", source_format="docx", total_chars=0,
        author_sheets_x1000=0, chapter_count=2, has_chapters="true", status="ready",
    ))
    for index, chapter_id in enumerate(("ch1", "ch2"), start=1):
        db.add(ScriptChapter(id=chapter_id, book_id=BOOK, chapter_index=index, chapter_title=f"Глава {index}",
                             status="published", source_text="Текст."))
        for ordinal, speaker in enumerate(SPEAKERS[chapter_id]):
            segment_id = f"{chapter_id}:{ordinal:05d}"
            db.add(V2Segment(id=segment_id, book_id=BOOK, chapter_id=chapter_id, ordinal=ordinal,
                             kind="paragraph", text="— Реплика.", char_start=0, char_end=10))
            db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, span_start=0, span_end=10,
                                 speaker=speaker, confidence=0.9, source="llm", version=1))
    db.add(Character(id="c1", book_id=BOOK, name="Химера", aliases="",
                     character_color="#cc0000", character_text_color="#ffffff",
                     actor_name="Иванова Мария"))
    db.add(Character(id="c2", book_id=BOOK, name="Дгарнин", aliases="",
                     character_color="#0000cc", character_text_color="#ffffff",
                     actor_name="Зотов Сергей"))
    db.commit()


def _payload(db, **kwargs):
    kwargs.setdefault("book_id", BOOK)
    kwargs.setdefault("chapter_id", "")
    kwargs.setdefault("user_id", "")
    kwargs.setdefault("user_name", "")
    return build_recording_workspace_payload(db, **kwargs)


def test_the_book_carries_the_code_uploads_are_tagged_with():
    """The screen had a text field defaulting to «SV2» with a TODO beside it.

    A wrong code files the audio under a book that does not exist, and the DAW export
    groups by that string, so the chapter would come back empty at mixing time.
    """
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        assert _payload(db)["selected_book"]["code"] == "КП"


def test_an_actor_sees_their_own_roles_before_uploading_anything():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        payload = _payload(db, user_name="Иванова Мария")

        assert [role["name"] for role in payload["my_roles"]] == ["Химера"]
        assert payload["my_roles"][0]["lines"] == 2


def test_the_cast_comes_with_the_colours_the_author_chose():
    """Otherwise the actor reads a script coloured differently from the author's."""
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        cast = {item["name"]: item for item in _payload(db)["cast"]}

        assert cast["Химера"]["color"] == "#cc0000"
        assert cast["Химера"]["lines"] == 2
        assert cast["Дгарнин"]["actor_name"] == "Зотов Сергей"


def test_choosing_a_chapter_changes_whose_lines_are_counted():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        payload = _payload(db, chapter_id="ch2", user_name="Зотов Сергей")

        assert payload["selected_chapter"]["id"] == "ch2"
        assert payload["my_roles"][0]["name"] == "Дгарнин"
        assert payload["my_roles"][0]["lines"] == 2


def test_an_actor_with_no_roles_gets_the_cast_anyway():
    # They still need to see the script, and an operator may record any role.
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        payload = _payload(db, user_name="Посторонний Человек")

        assert payload["my_roles"] == []
        assert len(payload["cast"]) == 2
