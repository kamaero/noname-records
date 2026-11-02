import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import BotIntro, User
from scripts import bot_ask_intros


def _setup(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr("app.db.SessionLocal", factory)
    with factory() as db:
        db.add(User(id="u", login="tg_3", password_hash="x", display_name="Уже есть", is_active="true"))
        db.commit()
    voices = tmp_path / "voices.json"
    voices.write_text(json.dumps({"speakers": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}, {"id": 3, "name": "C"}]}),
                      encoding="utf-8")
    checked = []

    def check(rows, *, token):
        checked.append([r["telegram_user_id"] for r in rows])
        return {"items": [{"telegram_user_id": "1", "display_name": "A", "username": "a", "reachable": True},
                          {"telegram_user_id": "2", "display_name": "B", "username": "b", "reachable": False}]}
    return factory, str(voices), check, checked


def test_list_shows_reachable_strangers_and_sends_nothing(tmp_path, monkeypatch, capsys):
    factory, voices, check, checked = _setup(tmp_path, monkeypatch)
    sent = []
    assert bot_ask_intros.main(["--list", "--json", voices], check=check, send=lambda m, p: sent.append(p)) == 0
    assert checked == [["1", "2"]] and sent == []
    assert "1" in capsys.readouterr().out
    with factory() as db:
        assert db.query(BotIntro).count() == 0


def test_send_asks_only_the_reachable(tmp_path, monkeypatch):
    factory, voices, check, _ = _setup(tmp_path, monkeypatch)
    sent = []
    bot_ask_intros.main(["--send", "--json", voices], check=check, send=lambda m, p: sent.append((m, p)) or True)
    assert [(m, p["chat_id"]) for m, p in sent] == [("sendMessage", "1")]
    with factory() as db:
        assert [r.telegram_user_id for r in db.query(BotIntro)] == ["1"]
