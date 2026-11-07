"""Ссылка диктора не должна вести к тёзке или прежнему владельцу username."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import BotContact, DictorProfile, TelegramAuthAccount, User, UserRole
from app.services import bot_contacts, dictors


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(User(id="actor", login="actor", display_name="Example Actor", password_hash="x", is_active="true"))
        session.add(UserRole(user_id="actor", role="dictor"))
        session.commit()
        yield session


def account(db, tid="42", active="true"):
    db.add(TelegramAuthAccount(telegram_user_id=tid, user_id="actor", role="dictor", access_scope="full",
                               display_name="Example Actor", is_active=active))
    db.commit()


def contact(db, tid="42", username="current_voice"):
    db.add(BotContact(telegram_user_id=tid, username=username, tg_name="Different Name"))
    db.commit()


def both(db):
    listing = dictors.list_dictors(db)[0]
    card = dictors.dictor_card(db, "actor")
    assert (listing["telegram_user_id"], listing["username"]) == (card["telegram_user_id"], card["username"])
    return listing["telegram_user_id"], listing["username"]


def test_exact_active_account_uses_bot_contact_over_stale_profile(db):
    account(db)
    contact(db)
    contact(db, "99", "namesake_voice")
    db.add(DictorProfile(user_id="actor", telegram_username="former_voice"))
    db.commit()
    assert both(db) == ("42", "current_voice")


def test_matching_contact_name_does_not_link_an_unbound_actor(db):
    contact(db)
    db.get(BotContact, "42").tg_name = "Example Actor"
    db.commit()
    assert both(db) == ("", "")


def test_no_contact_does_not_trust_a_manual_username_without_provenance(db):
    account(db)
    db.add(DictorProfile(user_id="actor", telegram_username=" @manual_voice "))
    db.commit()
    assert both(db) == ("42", "")


@pytest.mark.parametrize("username", ["", "https://t.me/other_voice", "evil?start=login", "share", "12345", "a" * 33])
def test_empty_or_invalid_contact_does_not_fall_back_to_old_profile(db, username):
    account(db)
    contact(db, username=username)
    db.add(DictorProfile(user_id="actor", telegram_username="former_voice"))
    db.commit()
    assert both(db) == ("42", "")


def test_disabled_or_ambiguous_binding_has_no_chat_target(db):
    account(db, active="false")
    contact(db)
    assert both(db) == ("", "")
    db.query(TelegramAuthAccount).filter_by(telegram_user_id="42").one().is_active = "true"
    db.commit()
    account(db, tid="43")
    assert both(db) == ("", "")


def test_inactive_actor_has_no_chat_target(db):
    account(db)
    contact(db)
    db.get(User, "actor").is_active = "false"
    db.commit()
    assert both(db) == ("", "")


def test_new_telegram_event_clears_removed_username(db):
    account(db)
    contact(db)
    bot_contacts.touch(db, {"id": 42, "first_name": "Example"}, now=datetime(2026, 1, 1))
    db.commit()
    assert db.get(BotContact, "42").username == ""
    assert both(db) == ("42", "")


def test_rebinding_and_unlinking_never_reuses_the_old_profile_alias(db):
    account(db)
    contact(db)
    db.add(DictorProfile(user_id="actor", telegram_username="current_voice"))
    db.commit()
    assert both(db) == ("42", "current_voice")
    old = db.query(TelegramAuthAccount).filter_by(telegram_user_id="42").one()
    old.user_id = ""
    db.commit()
    assert both(db) == ("", "")
    account(db, tid="43")
    assert both(db) == ("43", "")
    contact(db, tid="43", username="new_voice")
    assert both(db) == ("43", "new_voice")
