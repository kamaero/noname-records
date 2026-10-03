import json

from app.db import Base, SessionLocal, engine
from app.models import AuthorCharacter
from app.services import author_profile as ap


def _reset():
    Base.metadata.create_all(bind=engine)


def test_normalize_name():
    assert ap.normalize_name("  Хубол  ") == "хубол"
    assert ap.normalize_name("Лиу   Тайн") == "лиу тайн"


def test_get_or_create_author_is_idempotent():
    _reset()
    with SessionLocal() as db:
        a1 = ap.get_or_create_author(db, "belozerov", "Александр Белозёров")
        a2 = ap.get_or_create_author(db, "belozerov", "Александр Белозёров")
        db.commit()
        assert a1.id == a2.id


def test_upsert_and_find_by_name_and_alias():
    _reset()
    with SessionLocal() as db:
        a = ap.get_or_create_author(db, "belozerov_upsert", "Белозёров")
        ch, created = ap.upsert_character(
            db, a.id, canonical_name="Хубол", aliases=["Полумракский Купец"],
            description="Купец", source_topic="Бароны", status="confirmed",
        )
        db.commit()
        assert created is True
        assert ap.find_character(db, a.id, "хубол").id == ch.id
        assert ap.find_character(db, a.id, "Полумракский Купец").id == ch.id
        ch2, created2 = ap.upsert_character(
            db, a.id, canonical_name="Хубол", aliases=["Купец Душ"],
            description="", source_topic="", status="confirmed",
        )
        db.commit()
        assert created2 is False and ch2.id == ch.id
        assert set(json.loads(ch2.aliases)) == {"Полумракский Купец", "Купец Душ"}


def test_set_actor_and_color_non_overwrite():
    _reset()
    with SessionLocal() as db:
        a = ap.get_or_create_author(db, "belozerov_actor", "Белозёров")
        ch, _ = ap.upsert_character(db, a.id, canonical_name="Агг", aliases=[],
                                    description="", source_topic="", status="confirmed")
        assert ap.set_actor(ch, "Зарецкий Мирон") == "set"
        assert ap.set_actor(ch, "Зарецкий Мирон") == "noop"
        assert ap.set_actor(ch, "Кто-то Другой") == "conflict"
        assert ch.actor_name == "Зарецкий Мирон"
        assert ap.set_color(ch, "#cc0000") == "set"
        assert ap.set_color(ch, "#000000") == "conflict"


def test_upsert_pronunciation_idempotent():
    _reset()
    with SessionLocal() as db:
        a = ap.get_or_create_author(db, "belozerov_pron", "Белозёров")
        ap.upsert_pronunciation(db, a.id, "Полумрак", "Парго́рон", [])
        ap.upsert_pronunciation(db, a.id, "полумрак ", "Парго́рон", [])
        db.commit()
        rows = db.query(ap.AuthorPronunciation).filter_by(author_id=a.id).all()
        assert len(rows) == 1


def test_upsert_pronunciation_updates_a_changed_stress():
    # Until 2026-09 the first spelling won forever, so a correction never landed.
    _reset()
    with SessionLocal() as db:
        a = ap.get_or_create_author(db, "belozerov_pron_update", "Белозёров")
        first, created = ap.upsert_pronunciation(db, a.id, "Дгарнин", "Дга́рнин", [])
        second, created_again = ap.upsert_pronunciation(db, a.id, "дгарнин", "Дгарни́н", ["дгарнина"], source="book")
        db.commit()
        assert created is True and created_again is False
        assert second.id == first.id
        assert second.stressed == "Дгарни́н"
        assert json.loads(second.variants) == ["дгарнина"]
        assert second.source == "book"
        assert db.query(ap.AuthorPronunciation).filter_by(author_id=a.id).count() == 1


def test_upsert_pronunciation_can_be_told_not_to_overwrite():
    _reset()
    with SessionLocal() as db:
        a = ap.get_or_create_author(db, "belozerov_pron_keep", "Белозёров")
        ap.upsert_pronunciation(db, a.id, "Дгарнин", "Дга́рнин", [])
        row, created = ap.upsert_pronunciation(db, a.id, "Дгарнин", "Дгарни́н", [], overwrite=False)
        db.commit()
        assert created is False and row.stressed == "Дга́рнин"
