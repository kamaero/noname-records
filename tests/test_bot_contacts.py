"""Кто вышел на связь с ботом: учёт контактов и сводка владельцу раз в 30 минут (01.10)."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import AuditLog, BotContact, TelegramAuthAccount, User, UserRole
from app.services import bot_contacts, onboarding
from app.services.bot_dialog import handle_update

NOW = datetime(2026, 10, 1, 20, 0, 0)


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "owner_telegram_id", "900000100")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        for uid, name, role, tid in (("admin", "Тимур Садыков", "admin", "900000100"), ("u1", "Рябова Дарья", "dictor", "41")):
            session.add(User(id=uid, login=uid, password_hash="x", display_name=name, is_active="true"))
            session.add(UserRole(user_id=uid, role=role))
            session.add(TelegramAuthAccount(telegram_user_id=tid, role=role, access_scope="full", display_name=name,
                                            is_active="true", user_id=uid))
        session.commit()
        yield session


def _msg(text, tid, first="Имя", last="", username=""):
    sender = {"id": int(tid), "first_name": first, "last_name": last}
    if username:
        sender["username"] = username
    return {"update_id": 1, "message": {"message_id": 1, "from": sender, "chat": {"id": int(tid), "type": "private"},
                                        "text": text}}


def _report(db, now=NOW):
    sent = []
    bot_contacts.report_pass(db, now=now, send=lambda chat, text: sent.append((chat, text)) or True)
    db.commit()
    return sent


def test_every_message_records_the_contact_once(db):
    handle_update(db, _msg("/start dictor", "41", first="Дарья"), now=NOW)
    handle_update(db, _msg("привет", "41", first="Дарья"), now=NOW + timedelta(minutes=5))
    row = db.get(BotContact, "41")
    assert (row.first_seen_at, row.last_seen_at, row.tg_name) == (NOW, NOW + timedelta(minutes=5), "Дарья")


def test_the_report_names_known_and_unknown_people(db):
    handle_update(db, _msg("/start dictor", "41", first="Дарья"), now=NOW)
    handle_update(db, _msg("/start", "795", first="Лана", last="Ро", username="lanaro_voice"), now=NOW)
    handle_update(db, _msg("/start", "999", first="Кто-то", username="stranger"), now=NOW)
    sent = _report(db, now=NOW + timedelta(minutes=31))
    assert [chat for chat, _ in sent] == ["900000100"]
    text = sent[0][1]
    assert "Рябова Дарья" in text
    assert "Лана Ро @lanaro_voice (id 795)" in text
    assert "Кто-то @stranger (id 999)" in text


def test_nothing_new_means_no_report_and_each_contact_is_reported_once(db):
    assert _report(db, now=NOW + timedelta(minutes=31)) == []
    handle_update(db, _msg("/start", "41"), now=NOW + timedelta(minutes=40))
    assert len(_report(db, now=NOW + timedelta(minutes=62))) == 1
    handle_update(db, _msg("ещё", "41"), now=NOW + timedelta(minutes=70))
    assert _report(db, now=NOW + timedelta(minutes=93)) == []


def test_reports_come_at_most_every_30_minutes(db):
    handle_update(db, _msg("/start", "41"), now=NOW)
    assert len(_report(db, now=NOW + timedelta(minutes=31))) == 1
    handle_update(db, _msg("/start", "999"), now=NOW + timedelta(minutes=32))
    assert _report(db, now=NOW + timedelta(minutes=40)) == []
    assert len(_report(db, now=NOW + timedelta(minutes=62))) == 1


def test_password_issues_are_counted_and_repeats_flagged(db):
    for minute in (1, 2, 3):
        db.add(AuditLog(user_id="u1", entity_type="user", entity_id="u1", action="password_issued_by_bot",
                        payload_json="{}", created_at=NOW + timedelta(minutes=minute)))
    db.commit()
    text = _report(db, now=NOW + timedelta(minutes=31))[0][1]
    assert "Рябова Дарья ×3" in text


def test_the_admin_asks_who_and_a_dictor_cannot(db):
    handle_update(db, _msg("/start", "999", first="Кто-то"), now=NOW)
    out = handle_update(db, _msg("/кто", "900000100"), now=NOW + timedelta(minutes=1))
    assert "Кто-то" in out[0].payload["text"]
    out = handle_update(db, _msg("/кто", "41"), now=NOW + timedelta(minutes=1))
    assert "Кто-то" not in out[0].payload["text"]
