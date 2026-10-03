"""The profile page's numbers for a book, and the two syncs with their audit rows."""
import json
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Author, AuthorCharacter, AuthorPronunciation, Character, OperatorIntervention, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment, V2StressMark
from app.v2.profile_ops import apply_from_author, book_profile, sync_to_author

BOOK, AUTHOR, CH1 = "book-1", "author-1", "ch-1"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed(db, *, author_id=AUTHOR):
    db.add(Author(id=AUTHOR, name="Александр Белозёров", slug="belozerov"))
    db.add(ScriptBook(id=BOOK, title="Крылья", display_title="Крылья полумрака", source_filename="k.txt", source_format="txt",
                      author_id=author_id, pipeline_mode="standard", validation_profile="author_review",
                      pronunciation_notes="дгарнин=дгарни́н\nпупип=пупи́п\nмусор\n"))
    db.add(ScriptChapter(id=CH1, book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
    db.add(AuthorCharacter(id="ac1", author_id=AUTHOR, canonical_name="Дгарнин", aliases="[]", status="confirmed", reply_color="#00ff00"))
    db.add(AuthorCharacter(id="ac2", author_id=AUTHOR, canonical_name="Пупип", aliases="[]", status="unconfirmed"))
    db.add(AuthorPronunciation(id="p1", author_id=AUTHOR, term="Полумрак", stressed="Парго́рон", variants="[]"))
    db.add(Character(id="c1", book_id=BOOK, name="Дгарнин", author_character_id="ac1", character_color=""))
    db.add(Character(id="c2", book_id=BOOK, name="Пупип", author_character_id=""))
    db.add(Character(id="c3", book_id=BOOK, name="Новый", author_character_id=""))
    for i in range(3):
        seg = f"{CH1}:{i:05d}"
        db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=CH1, ordinal=i, text="слово", char_end=5))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=f"{CH1}:00000", span_start=0, span_end=5, speaker="Дгарнин", version=1))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=f"{CH1}:00000", span_start=0, span_end=5, speaker="Пупип", version=2))
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=f"{CH1}:00001", span_start=0, span_end=5, speaker="Пупип", version=1))
    # Two marks in version 1, one corrected in version 2: effective count is 1.
    db.add(V2StressMark(id="s1", segment_id=f"{CH1}:00000", word_start=0, word_end=5, vowel_offset=2, version=1))
    db.add(V2StressMark(id="s2", segment_id=f"{CH1}:00001", word_start=0, word_end=5, vowel_offset=2, version=1))
    db.add(V2StressMark(id="s3", segment_id=f"{CH1}:00001", word_start=0, word_end=5, vowel_offset=4, version=2))
    db.commit()
    return db.get(ScriptBook, BOOK)


def test_book_profile_counts():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        profile = book_profile(db, BOOK)

    assert profile["book"] == {"id": BOOK, "title": "Крылья полумрака", "pipeline_mode": "standard",
                               "validation_profile": "author_review", "author_id": AUTHOR}
    assert profile["author"] == {"id": AUTHOR, "name": "Александр Белозёров", "slug": "belozerov"}
    assert profile["counts"] == {
        "author_characters": 2,
        "author_characters_confirmed": 1,
        "linked_characters": 1,
        "author_pronunciations": 1,
        "book_pronunciation_terms": 2,
        "v2_segments": 3,
        "v2_attributed_segments": 2,
        "v2_stress_marks": 2,
    }
    assert profile["last_sync"] is None


def test_book_profile_without_author_and_unknown_book():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db, author_id="")
        profile = book_profile(db, BOOK)
        assert profile["author"] is None
        assert profile["counts"]["author_characters"] == 0 and profile["counts"]["author_pronunciations"] == 0
        assert profile["counts"]["linked_characters"] == 1
        assert book_profile(db, "nope") is None


def test_sync_records_an_intervention_that_the_profile_reports_as_last_sync():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db)
        summary = sync_to_author(db, book, "u1", "Автор")
        db.commit()

        assert summary["characters_created"] == 1 and summary["characters_linked"] == 2
        assert summary["pronunciations_added"] == 2
        row = db.query(OperatorIntervention).one()
        assert row.action_type == "v2_profile_sync" and row.actor_user_id == "u1" and row.actor_name == "Автор"
        assert json.loads(row.payload_json) == summary

        profile = book_profile(db, BOOK)
        assert profile["last_sync"]["summary"] == summary
        assert profile["last_sync"]["at"]
        assert profile["counts"]["linked_characters"] == 3
        assert profile["counts"]["author_pronunciations"] == 3


def test_apply_records_its_own_intervention_and_honours_overwrite():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db)
        summary = apply_from_author(db, book, overwrite=False, actor_uid="u1")
        db.commit()
        assert summary["colours_applied"] == 1 and summary["overwrite"] is False
        assert db.get(Character, "c1").character_color == "#00ff00"
        assert summary["pronunciations_applied"] == 1
        assert "Полумрак=Парго́рон" in book.pronunciation_notes

        rows = db.query(OperatorIntervention).all()
        assert [r.action_type for r in rows] == ["v2_profile_apply"]
        assert book_profile(db, BOOK)["last_sync"] is None, "an apply is not a sync"
