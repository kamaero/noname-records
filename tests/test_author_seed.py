from app.db import Base, SessionLocal, engine
from app.models import Character, ScriptBook
from app.services import author_profile as ap
from app.services import author_seed


def _reset():
    Base.metadata.create_all(bind=engine)


def _book_with_author(db, slug):
    a = ap.get_or_create_author(db, slug, "Белозёров")
    book = ScriptBook(title="t", display_title="t", source_filename="t.txt", source_format="txt", author_id=a.id)
    db.add(book); db.flush()
    return a, book


def test_roster_text_confirmed_only_compact():
    _reset()
    with SessionLocal() as db:
        a = ap.get_or_create_author(db, "belozerov_roster", "Белозёров")
        ap.upsert_character(db, a.id, canonical_name="Хубол", aliases=["Полумракский Купец"], status="confirmed")
        ap.upsert_character(db, a.id, canonical_name="Агг", aliases=[], status="confirmed")
        ap.upsert_character(db, a.id, canonical_name="Шум", aliases=[], status="unconfirmed")
        db.commit()
        text = author_seed.build_author_roster_text(db, a.id)
        assert "Хубол (Полумракский Купец)" in text
        assert "Агг" in text
        assert "Шум" not in text
        assert text.count(";") == 1


def test_roster_text_empty_without_author():
    _reset()
    with SessionLocal() as db:
        assert author_seed.build_author_roster_text(db, "") == ""


def test_roster_system_suffix_wraps_or_empty():
    _reset()
    with SessionLocal() as db:
        a = ap.get_or_create_author(db, "belozerov_suffix", "Белозёров")
        ap.upsert_character(db, a.id, canonical_name="Хубол", aliases=["Купец"], status="confirmed")
        db.commit()
        suffix = author_seed.author_roster_system_suffix(db, a.id)
        assert "ИЗВЕСТНЫЙ КАНОН АВТОРА" in suffix and "Хубол (Купец)" in suffix
        assert author_seed.author_roster_system_suffix(db, "") == ""


def test_link_matches_and_fills_color_actor_when_empty():
    _reset()
    with SessionLocal() as db:
        a, book = _book_with_author(db, "rud_link")
        ap.upsert_character(db, a.id, canonical_name="Хубол", aliases=["Полумракский Купец"], status="confirmed")
        canon = ap.find_character(db, a.id, "Хубол")
        canon.reply_color = "#660000"; canon.actor_name = "Белозёров Александр"; db.flush()
        ch = Character(book_id=book.id, name="Полумракский Купец")
        db.add(ch); db.flush()
        result = author_seed.link_character_to_author(db, book, ch)
        assert result == {"matched": True, "color_set": True, "actor_set": True}
        assert ch.author_character_id == canon.id
        assert ch.character_color == "#660000"
        assert ch.actor_name == "Белозёров Александр"


def test_link_never_overwrites_existing_book_values():
    _reset()
    with SessionLocal() as db:
        a, book = _book_with_author(db, "rud_link2")
        ap.upsert_character(db, a.id, canonical_name="Агг", aliases=[], status="confirmed")
        canon = ap.find_character(db, a.id, "Агг")
        canon.actor_name = "Канон Актёр"; canon.reply_color = "#111111"; db.flush()
        ch = Character(book_id=book.id, name="Агг", actor_name="Уже Назначен", character_color="#abcabc")
        db.add(ch); db.flush()
        result = author_seed.link_character_to_author(db, book, ch)
        assert result["matched"] is True and result["actor_set"] is False and result["color_set"] is False
        assert ch.actor_name == "Уже Назначен" and ch.character_color == "#abcabc"
        assert ch.author_character_id == canon.id


def test_link_noop_without_author_or_match():
    _reset()
    with SessionLocal() as db:
        book_no_author = ScriptBook(title="t", display_title="t", source_filename="t.txt", source_format="txt")
        db.add(book_no_author); db.flush()
        ch = Character(book_id=book_no_author.id, name="Нечто")
        db.add(ch); db.flush()
        assert author_seed.link_character_to_author(db, book_no_author, ch) == {
            "matched": False, "color_set": False, "actor_set": False}
