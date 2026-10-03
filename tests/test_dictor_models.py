# tests/test_dictor_models.py
"""Таблицы раздела «Дикторы» создаются и принимают строки."""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import DictorAssignment, DictorDemo, DictorLink, DictorProfile, Recast


def test_dictor_tables_accept_rows():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(DictorProfile(user_id="u1", telegram_username="lena_voice", note="хороша в комедии"))
        db.add(DictorDemo(id="d1", user_id="u1", title="старцы мудрые", stored_key="demos/u1/d1.mp3",
                          duration_seconds=64.0, size_bytes=1000, md5="x" * 32, trimmed=False,
                          source="import", source_url="", source_ref="900000112:253060"))
        db.add(DictorLink(user_id="u1", url="https://t.me/example_voice", title="канал"))
        db.add(DictorAssignment(user_id="u1", book_id="b1", character_id="c1", role_name="Куйбу",
                                state="approved", recorded=False))
        db.add(Recast(author_character_id="a1", role_name="Куйбу", from_actor="Сомов Роман",
                      to_actor="Иванов Иван", reason="left", comment="", books_changed="[]",
                      books_kept="[]", actor_user_id="admin"))
        db.commit()
        assert db.get(DictorProfile, "u1").note == "хороша в комедии"
        assert db.query(DictorDemo).one().trimmed is False
