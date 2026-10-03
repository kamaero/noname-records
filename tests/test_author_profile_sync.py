"""Lift a book's work up into the author's profile.

Today the profile only ever hands data DOWN: the author roster seeds char_extraction.
Nothing goes back up, so «Крылья полумрака» sits with 221 characters — 48 with an
assigned actor, all with a colour — while Белозёров's profile holds 1741 characters and
only 62 colours, and zero pronunciations. The link column characters.author_character_id
exists in the schema and is empty on every row.

That matters right now: the author is about to spend days validating this book. Every
stress mark and colour he sets would land on the book alone and be gone by the next
one. The second Belozerov book has nothing to inherit unless this loop closes first.

Conflict rule, per the owner: the profile wins. A colour already agreed there is the
through-line and a book may not overwrite it — it only fills blanks. set_color and
set_actor already implement exactly that, returning "conflict" instead of writing.
"""
import json
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db import Base
from app.models import Author, AuthorCharacter, AuthorPronunciation, Character, ScriptBook
from app.services.author_profile import sync_book_to_author_profile

AUTHOR = "author-1"
BOOK = "book-1"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed(db, *, notes="", characters=()):
    db.add(Author(id=AUTHOR, name="Александр Белозёров", slug="belozerov"))
    db.add(ScriptBook(
        id=BOOK, title="Крылья", display_title="Крылья", source_filename="x.docx",
        source_format="docx", total_chars=0, author_sheets_x1000=0, chapter_count=1,
        has_chapters="true", status="author_review", author_id=AUTHOR,
        pronunciation_notes=notes,
    ))
    for i, (name, color, actor) in enumerate(characters):
        db.add(Character(
            id=f"c{i}", book_id=BOOK, name=name, aliases="",
            character_color=color, actor_name=actor,
        ))
    db.commit()
    return db.get(ScriptBook, BOOK)


def test_a_character_new_to_the_profile_is_created_and_linked():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, characters=[("Дгарни́н", "#1adab6", "Зотов Сергей")])
        report = sync_book_to_author_profile(db, book)
        db.commit()

        ac = db.query(AuthorCharacter).one()
        assert ac.canonical_name == "Дгарни́н"
        assert ac.reply_color == "#1adab6"
        assert ac.actor_name == "Зотов Сергей"
        # The link column finally gets filled.
        assert db.get(Character, "c0").author_character_id == ac.id
        assert report["characters_created"] == 1
        assert report["characters_linked"] == 1


def test_the_profile_colour_wins_over_the_book():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, characters=[("Дгарнин", "#1adab6", "")])
        db.add(AuthorCharacter(
            id="ac-1", author_id=AUTHOR, canonical_name="Дгарнин", aliases="[]",
            reply_color="#00ff00", actor_name="Зотов Сергей", status="confirmed",
        ))
        db.commit()

        report = sync_book_to_author_profile(db, book)
        db.commit()

        assert db.get(AuthorCharacter, "ac-1").reply_color == "#00ff00", "profile must win"
        assert report["colour_conflicts"] == 1
        # Still linked, so the book can start reading the through-line colour.
        assert db.get(Character, "c0").author_character_id == "ac-1"


def test_a_blank_in_the_profile_is_filled_from_the_book():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, characters=[("Химера", "#abcdef", "Иванова")])
        db.add(AuthorCharacter(
            id="ac-2", author_id=AUTHOR, canonical_name="Химера", aliases="[]",
            reply_color="", actor_name="", status="confirmed",
        ))
        db.commit()

        report = sync_book_to_author_profile(db, book)
        db.commit()

        ac = db.get(AuthorCharacter, "ac-2")
        assert ac.reply_color == "#abcdef"
        assert ac.actor_name == "Иванова"
        assert report["colours_set"] == 1
        assert report["actors_set"] == 1


def test_matching_is_alias_aware():
    # «Дгарни́н» with a stress mark must find «Дгарнин» in the profile, not duplicate it.
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, characters=[("Дгарни́н", "#111111", "")])
        db.add(AuthorCharacter(
            id="ac-3", author_id=AUTHOR, canonical_name="Кто-то",
            aliases=json.dumps(["Дгарни́н"], ensure_ascii=False),
            reply_color="", actor_name="", status="confirmed",
        ))
        db.commit()

        sync_book_to_author_profile(db, book)
        db.commit()

        assert db.query(AuthorCharacter).count() == 1, "must not create a duplicate"
        assert db.get(Character, "c0").author_character_id == "ac-3"


def test_stress_notes_become_author_pronunciations():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, notes="дгарнин=дгарни́н\nпупип=пупи́п\n")
        report = sync_book_to_author_profile(db, book)
        db.commit()

        terms = {p.term: p.stressed for p in db.query(AuthorPronunciation).all()}
        assert terms == {"дгарнин": "дгарни́н", "пупип": "пупи́п"}
        assert report["pronunciations_added"] == 2
        assert db.query(AuthorPronunciation).first().source == "book"


def test_malformed_notes_lines_are_skipped_not_fatal():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, notes="дгарнин=дгарни́н\nмусор без разделителя\n=\nпупип=пупи́п")
        report = sync_book_to_author_profile(db, book)
        db.commit()
        assert report["pronunciations_added"] == 2


def test_running_twice_changes_nothing_the_second_time():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, notes="дгарнин=дгарни́н\n",
                     characters=[("Дгарни́н", "#1adab6", "Зотов Сергей")])
        sync_book_to_author_profile(db, book)
        db.commit()
        second = sync_book_to_author_profile(db, book)
        db.commit()

        assert second["characters_created"] == 0
        assert second["pronunciations_added"] == 0
        assert db.query(AuthorCharacter).count() == 1
        assert db.query(AuthorPronunciation).count() == 1


def test_a_book_with_no_author_is_left_alone():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, characters=[("Дгарни́н", "#1adab6", "")])
        book.author_id = ""
        db.commit()

        report = sync_book_to_author_profile(db, book)
        assert report["skipped"] == "no_author"
        assert db.query(AuthorCharacter).count() == 0


def test_sync_does_not_overwrite_a_profile_stress_with_the_book_one():
    # The profile wins: a book's notes may add terms, never change what the profile settled.
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, notes="дгарнин=дга́рнин\n")
        db.add(AuthorPronunciation(id="p1", author_id=AUTHOR, term="Дгарнин", stressed="Дгарни́н", variants="[]", source="compendium"))
        db.commit()
        report = sync_book_to_author_profile(db, book)
        db.commit()
        assert report["pronunciations_added"] == 0
        assert db.get(AuthorPronunciation, "p1").stressed == "Дгарни́н"
