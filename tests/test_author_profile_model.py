from app.db import Base, SessionLocal, engine
from app.models import Author, AuthorCharacter, AuthorPronunciation, ScriptBook, Character


def _reset():
    Base.metadata.create_all(bind=engine)


def test_author_profile_tables_roundtrip():
    _reset()
    with SessionLocal() as db:
        author = Author(name="Александр Белозёров", slug="belozerov")
        db.add(author)
        db.flush()
        db.add(AuthorCharacter(
            author_id=author.id, canonical_name="Хубол",
            aliases='["Полумракский Купец"]', reply_color="#660000",
            actor_name="Белозёров Александр", source_topic="Полумрак. Бароны.",
            status="confirmed",
        ))
        db.add(AuthorPronunciation(
            author_id=author.id, term="Полумрак", stressed="Парго́рон", source="compendium",
        ))
        db.commit()
        ch = db.query(AuthorCharacter).filter_by(author_id=author.id).one()
        assert ch.canonical_name == "Хубол"
        assert ch.status == "confirmed"
        pr = db.query(AuthorPronunciation).filter_by(author_id=author.id).one()
        assert pr.stressed == "Парго́рон"


def test_new_link_columns_default_empty():
    _reset()
    with SessionLocal() as db:
        book = ScriptBook(title="t", display_title="t", source_filename="t.txt", source_format="txt")
        db.add(book)
        db.flush()
        assert book.author_id == ""
        ch = Character(book_id=book.id, name="X")
        db.add(ch)
        db.flush()
        assert ch.author_character_id == ""
