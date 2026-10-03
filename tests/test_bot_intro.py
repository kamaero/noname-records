from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import BotIntro, TelegramAuthAccount, User, UserRole
from app.services import bot_intro
from app.services.login_identity import TELEGRAM_PASSWORD_HASH

NOW = datetime(2026, 10, 1, 12, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        yield session


@pytest.mark.parametrize("raw,clean", [
    ("ветрова ольга", "Ветрова Ольга"),
    ("  Ветрова   Ольга  ", "Ветрова Ольга"),
    ("Римский-Корсаков Николай Аверинич", "Римский-Корсаков Николай Аверинич"),
    ("O'Brien Pat", "O'Brien Pat"),
    ("Ольга", ""),
    ("Ветрова Ольга 2", ""),
    ("Ветрова Ольга 🙂", ""),
    ("а б в г д", ""),
    ("Ааааааааааааааааааааааааааааааа Бббббббббббббббббббббббббббббббб", ""),
])
def test_clean_name(raw, clean):
    assert bot_intro.clean_name(raw) == clean


def test_a_stranger_may_introduce_but_not_an_existing_or_switched_off_one(db):
    assert bot_intro.may_introduce(db, "42") is True
    db.add(TelegramAuthAccount(telegram_user_id="43", role="dictor", access_scope="full", display_name="", is_active="false"))
    db.add(TelegramAuthAccount(telegram_user_id="44", role="dictor", access_scope="full", display_name="", is_active="true", user_id="u9"))
    db.add(TelegramAuthAccount(telegram_user_id="45", role="dictor", access_scope="full", display_name="", is_active="true"))
    db.add(User(id="u46", login="tg_46", password_hash=TELEGRAM_PASSWORD_HASH, display_name="Кто-то", is_active="true"))
    db.commit()
    assert [bot_intro.may_introduce(db, t) for t in ("43", "44", "45", "46", "abc")] == [False, False, True, False, False]


def test_asking_twice_keeps_one_fresh_question(db):
    bot_intro.ask(db, "42", now=NOW - timedelta(hours=30))
    assert bot_intro.intro_state(db, "42", now=NOW) == "stale"
    bot_intro.ask(db, "42", now=NOW)
    db.commit()
    assert db.query(BotIntro).count() == 1
    assert bot_intro.intro_state(db, "42", now=NOW + timedelta(hours=1)) == "fresh"
    assert bot_intro.intro_state(db, "99", now=NOW) is None


def test_create_dictor_makes_a_linked_tg_account_and_clears_the_question(db):
    bot_intro.ask(db, "42", now=NOW)
    made = bot_intro.create_dictor(db, "42", "Ветрова Ольга", now=NOW)
    db.commit()
    user = db.get(User, made["user_id"])
    assert (user.login, user.display_name, user.password_hash) == ("tg_42", "Ветрова Ольга", TELEGRAM_PASSWORD_HASH)
    assert [r.role for r in db.query(UserRole).filter_by(user_id=user.id)] == ["dictor"]
    row = db.query(TelegramAuthAccount).filter_by(telegram_user_id="42").one()
    assert row.user_id == user.id and row.is_active == "true"
    assert db.query(BotIntro).count() == 0 and made["twins"] == []


def test_a_namesake_is_reported_and_left_alone(db):
    db.add(User(id="old", login="olga", password_hash="x", display_name="Ольга Ветрова", is_active="true"))
    db.commit()
    made = bot_intro.create_dictor(db, "42", "Ветрова Ольга", now=NOW)
    db.commit()
    assert made["twins"] == ["Ольга Ветрова"] and made["user_id"] != "old"
    assert db.get(User, "old").password_hash == "x"
