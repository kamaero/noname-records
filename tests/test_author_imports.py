from app.db import Base, SessionLocal, engine
from app.services import author_profile as ap
from app.services.author_imports.compendium import import_compendium
from app.services.author_imports.cast import import_cast
from app.services.author_imports.legend import import_legend


def _reset():
    Base.metadata.create_all(bind=engine)


def test_full_bootstrap_flow():
    _reset()
    with SessionLocal() as db:
        author = ap.get_or_create_author(db, "belozerov_flow", "Белозёров")
        entries = [
            {"name": "Хубол", "aliases": ["Полумракский Купец"], "topic": "Бароны", "description": "Купец"},
            {"name": "Агг", "aliases": ["Столп Полумрака"], "topic": "Бароны", "description": "Великан"},
        ]
        s1 = import_compendium(db, author.id, entries)
        assert s1["created"] == 2 and s1["updated"] == 0
        s2 = import_cast(db, author.id, {"Полумракский Купец": "Белозёров Александр", "Новый": "Иванов Иван"})
        assert s2["actor_set"] == 1 and s2["created_unconfirmed"] == 1
        hubol = ap.find_character(db, author.id, "Хубол")
        assert hubol.actor_name == "Белозёров Александр" and hubol.status == "confirmed"
        s3 = import_legend(db, author.id, {"Агг": "#cc0000"})
        assert s3["color_set"] == 1
        agg = ap.find_character(db, author.id, "Агг")
        assert agg.reply_color == "#cc0000"
        db.commit()


def test_new_character_field_counted_once_not_double():
    """A brand-new character created by cast/legend carries its actor/colour but is
    counted only under created_unconfirmed — not also actor_set/color_set."""
    _reset()
    with SessionLocal() as db:
        author = ap.get_or_create_author(db, "belozerov_newchar", "Белозёров")
        sc = import_cast(db, author.id, {"Совсем Новый": "Актёр Икс"})
        assert sc == {"actor_set": 0, "created_unconfirmed": 1, "conflicts": [], "noop": 0}
        assert ap.find_character(db, author.id, "Совсем Новый").actor_name == "Актёр Икс"
        sl = import_legend(db, author.id, {"Цветной Новый": "#abcdef"})
        assert sl == {"color_set": 0, "created_unconfirmed": 1, "conflicts": [], "noop": 0}
        assert ap.find_character(db, author.id, "Цветной Новый").reply_color == "#abcdef"
        db.commit()
