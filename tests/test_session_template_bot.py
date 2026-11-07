"""Шаблон сессии присылают боту: .sesx от владельца — шаблон, а не проба и не демо.

25.10 владелец прислал шаблон, а бот его не взял: документ не аудио, от не-диктора —
ему было некуда лечь, и файл потерялся.
"""
import contextlib
import io
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import TelegramAuthAccount, User, UserRole
from app.services import onboarding, session_template
from app.services.bot_dialog import handle_update
from tests.test_session_template import VO, _slot, _template, _track

NOW = datetime(2026, 10, 25, 9, 0, 0)
OWNER = "900000100"


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", OWNER)
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        for uid, name, role, tid in (("uo", "Фернос Фарита", "admin", OWNER), ("u41", "Ветрова Ольга", "dictor", "41")):
            session.add(User(id=uid, login=uid, password_hash="x", display_name=name, is_active="true"))
            session.add(UserRole(user_id=uid, role=role))
            session.add(TelegramAuthAccount(telegram_user_id=tid, role=role, access_scope="full", display_name=name,
                                            is_active="true", user_id=uid))
        session.commit()
        yield session


def _doc(sender=OWNER, name="Шаблон.sesx", size=40_000):
    return {"update_id": 1, "message": {
        "message_id": 7, "from": {"id": int(sender)}, "chat": {"id": int(sender), "type": "private"},
        "document": {"file_id": "FT", "file_unique_id": "UT", "file_name": name,
                     "mime_type": "application/octet-stream", "file_size": size}}}


def test_the_owners_sesx_is_queued_as_the_template(db):
    out = handle_update(db, _doc(), now=NOW)
    assert [o.method for o in out] == ["sendMessage", "enqueue_session_template"]
    assert out[1].payload == {"file_id": "FT", "chat_id": int(OWNER)}


def test_a_dictors_sesx_is_not_a_template(db):
    out = handle_update(db, _doc(sender="41"), now=NOW)
    assert "enqueue_session_template" not in [o.method for o in out]


def test_an_oversized_file_is_refused_at_once(db):
    out = handle_update(db, _doc(size=session_template.MAX_TEMPLATE_BYTES + 1), now=NOW)
    assert [o.method for o in out] == ["sendMessage"] and "too large" in out[0].payload["text"]


def _fetch(data):
    @contextlib.contextmanager
    def fetch(file_id):
        yield io.BytesIO(data)
    return fetch


def test_the_worker_saves_it_and_names_the_roles(tmp_path, monkeypatch):
    monkeypatch.setattr(session_template, "TEMPLATE_PATH", str(tmp_path / "t.sesx"))
    sent = []
    data = _template(_track("Рассказчик", [VO(0), _slot(1, "Pitch Shifter")]), _track("Хдыбдяр", [VO(0)])).encode()
    session_template.store_from_telegram("FT", 5, fetch=_fetch(data), send_text=lambda c, t: sent.append(t) or True)
    assert session_template.load_template().roles == ["Рассказчик", "Хдыбдяр"]
    assert "Рассказчик, Хдыбдяр" in sent[0] and "Voice Over" in sent[0]


def test_the_reply_names_tracks_no_book_has(tmp_path, monkeypatch):
    """«Хдыдяр» вместо «Хдыбдяр» молча не лёг бы никуда — владелец должен увидеть сразу."""
    monkeypatch.setattr(session_template, "TEMPLATE_PATH", str(tmp_path / "t.sesx"))
    sent = []
    data = _template(_track("Рассказчик", [VO(0)]), _track("Хдыдяр", [VO(0)])).encode()
    session_template.store_from_telegram("FT", 5, fetch=_fetch(data), send_text=lambda c, t: sent.append(t) or True,
                                         known_roles=["Рассказчик", "Хдыбдяр"])
    assert any("Хдыдяр" in line and "do not occur" in line for line in sent[0].split("\n"))


def test_a_bad_template_is_explained_and_the_old_one_stays(tmp_path, monkeypatch):
    monkeypatch.setattr(session_template, "TEMPLATE_PATH", str(tmp_path / "t.sesx"))
    session_template.save_template(_template(_track("Рассказчик", [VO(0)])).encode())
    sent = []
    session_template.store_from_telegram("FT", 5, fetch=_fetch(b"<html/>"), send_text=lambda c, t: sent.append(t) or True)
    assert "not an Audition session" in sent[0] and session_template.load_template().roles == ["Рассказчик"]
