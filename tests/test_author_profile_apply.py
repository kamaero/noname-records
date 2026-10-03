"""Apply the author's settled profile down onto a new book.

The mirror of sync_book_to_author_profile. Together they close the loop: a book's
decisions rise into the profile, and the next book of the same author starts with
them already in place — the same colour for Дгарнин across every book, the same
stress marks, the same actor.

Direction rule, and it is the opposite of the sync's: here the PROFILE is the source
and the book the destination, so the profile fills the book. But it fills only blanks
and never overwrites a value the book already carries — a colour already on a book's
character was put there by somebody, and a sync running later must not undo their
work.
"""
import os
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db import Base
from app.models import Author, AuthorCharacter, AuthorPronunciation, Character, ScriptBook
from app.services.author_profile import apply_author_profile_to_book

AUTHOR = "author-1"
BOOK = "book-2"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _seed(db, *, notes="", book_chars=(), profile_chars=(), pronunciations=()):
    db.add(Author(id=AUTHOR, name="Александр Белозёров", slug="belozerov"))
    db.add(ScriptBook(
        id=BOOK, title="Вторая книга", display_title="Вторая книга",
        source_filename="x.docx", source_format="docx", total_chars=0,
        author_sheets_x1000=0, chapter_count=1, has_chapters="true",
        status="processing", author_id=AUTHOR, pronunciation_notes=notes,
    ))
    for i, (name, color, actor) in enumerate(book_chars):
        db.add(Character(id=f"c{i}", book_id=BOOK, name=name, aliases="",
                         character_color=color, actor_name=actor))
    for i, (name, color, actor) in enumerate(profile_chars):
        db.add(AuthorCharacter(id=f"ac{i}", author_id=AUTHOR, canonical_name=name,
                               aliases="[]", reply_color=color, actor_name=actor,
                               status="confirmed"))
    for term, stressed in pronunciations:
        db.add(AuthorPronunciation(author_id=AUTHOR, term=term, stressed=stressed,
                                   variants="[]", source="book"))
    db.commit()
    return db.get(ScriptBook, BOOK)


def test_the_profile_colour_reaches_a_new_book():
    """Дгарнин must look the same in every Belozerov book."""
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db,
                     book_chars=[("Дгарни́н", "", "")],
                     profile_chars=[("Дгарнин", "#00ff00", "Зотов Сергей")])
        report = apply_author_profile_to_book(db, book)
        db.commit()

        ch = db.get(Character, "c0")
        assert ch.character_color == "#00ff00"
        assert ch.actor_name == "Зотов Сергей"
        assert ch.author_character_id == "ac0"
        assert report["colours_applied"] == 1
        assert report["actors_applied"] == 1


def test_a_colour_the_book_already_has_is_not_overwritten():
    # Somebody chose it. A later sync must not undo their work.
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db,
                     book_chars=[("Дгарнин", "#123456", "Другой актёр")],
                     profile_chars=[("Дгарнин", "#00ff00", "Зотов Сергей")])
        report = apply_author_profile_to_book(db, book)
        db.commit()

        ch = db.get(Character, "c0")
        assert ch.character_color == "#123456"
        assert ch.actor_name == "Другой актёр"
        assert report["colours_applied"] == 0
        # Linking still happens — the two rows are the same character either way.
        assert ch.author_character_id == "ac0"


def test_a_character_absent_from_the_profile_is_left_for_the_palette():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, book_chars=[("Новичок", "", "")], profile_chars=[])
        report = apply_author_profile_to_book(db, book)
        db.commit()

        assert db.get(Character, "c0").character_color == ""
        assert report["colours_applied"] == 0


def test_author_stress_marks_are_merged_into_the_book_notes():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, notes="своё=сво́ё\n",
                     pronunciations=[("дгарнин", "дгарни́н"), ("пупип", "пупи́п")])
        report = apply_author_profile_to_book(db, book)
        db.commit()

        notes = db.get(ScriptBook, BOOK).pronunciation_notes
        assert "своё=сво́ё" in notes, "the book's own note must survive"
        assert "дгарнин=дгарни́н" in notes
        assert "пупип=пупи́п" in notes
        assert report["pronunciations_applied"] == 2


def test_a_stress_the_book_already_defines_is_not_replaced():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, notes="дгарнин=дгарнин́\n",
                     pronunciations=[("дгарнин", "дгарни́н")])
        report = apply_author_profile_to_book(db, book)
        db.commit()

        notes = db.get(ScriptBook, BOOK).pronunciation_notes
        assert notes.count("дгарнин=") == 1
        assert "дгарнин=дгарнин́" in notes
        assert report["pronunciations_applied"] == 0


def test_running_twice_changes_nothing_the_second_time():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db,
                     book_chars=[("Дгарни́н", "", "")],
                     profile_chars=[("Дгарнин", "#00ff00", "Зотов Сергей")],
                     pronunciations=[("пупип", "пупи́п")])
        apply_author_profile_to_book(db, book)
        db.commit()
        second = apply_author_profile_to_book(db, book)
        db.commit()

        assert second["colours_applied"] == 0
        assert second["pronunciations_applied"] == 0


def test_overwrite_replaces_a_colour_the_book_generated():
    """«Крылья» got its 221 colours from the palette, not from a person.

    Default behaviour must stay "fill blanks only" — it protects real decisions. But a
    book whose colours were auto-generated has nothing worth protecting, and without
    this the through-line never reaches the first book that already has placeholders.
    The caller states which case it is; the function does not guess.
    """
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db,
                     book_chars=[("Агг", "#000000", "")],
                     profile_chars=[("Агг", "#cc0000", "Зарецкий Мирон")])
        report = apply_author_profile_to_book(db, book, overwrite=True)
        db.commit()

        ch = db.get(Character, "c0")
        assert ch.character_color == "#cc0000"
        assert ch.actor_name == "Зарецкий Мирон"
        assert report["colours_applied"] == 1


def test_overwrite_still_leaves_alone_what_the_profile_does_not_know():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db,
                     book_chars=[("Незнакомец", "#123456", "Актёр Книги")],
                     profile_chars=[("Агг", "#cc0000", "Зарецкий Мирон")])
        apply_author_profile_to_book(db, book, overwrite=True)
        db.commit()

        ch = db.get(Character, "c0")
        assert ch.character_color == "#123456"
        assert ch.actor_name == "Актёр Книги"


def test_a_stress_mark_does_not_hide_a_character_from_the_profile():
    """The bug this whole pairing would have died on.

    normalize_name did not strip combining marks, so «Дгарни́н» in a book never matched
    «Дгарнин» in the profile. Every stressed name looked new — which is precisely why
    all 221 rows of «Крылья полумрака» sat unlinked. Both directions depend on this.
    """
    from app.services.author_profile import normalize_name

    assert normalize_name("Дгарни́н") == normalize_name("Дгарнин")
    assert normalize_name("Химе́ра") == normalize_name("химера")
    # Distinct characters must still be distinct.
    assert normalize_name("Дгарнин") != normalize_name("Пупип")


def test_a_book_with_no_author_is_left_alone():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, book_chars=[("Дгарнин", "", "")])
        book.author_id = ""
        db.commit()
        report = apply_author_profile_to_book(db, book)
        assert report["skipped"] == "no_author"


def test_an_actor_from_the_profile_gets_the_role_in_his_assignments():
    # 30.09: СВ3 и СВ4 получили каст из профиля, а «Мои роли» дикторов о них молчали —
    # назначения пересобирались только при ручной правке каста.
    from app.models import DictorAssignment, User, UserRole

    SessionLocal = _session()
    with SessionLocal() as db:
        db.add(User(id="u-nr", login="roman", password_hash="", display_name="Валиев Роман",
                    is_active="true"))
        db.add(UserRole(id="r-nr", user_id="u-nr", role="dictor"))
        book = _seed(db,
                     book_chars=[("Бапдиг", "", "")],
                     profile_chars=[("Бапдиг", "#aa0000", "Валиев Роман")])
        apply_author_profile_to_book(db, book)
        db.commit()

        rows = db.query(DictorAssignment).filter_by(book_id=BOOK).all()
        assert [(r.user_id, r.role_name, r.state) for r in rows] == [("u-nr", "Бапдиг", "approved")]
