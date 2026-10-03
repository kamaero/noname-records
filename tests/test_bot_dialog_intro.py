from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import BotIntro, TelegramAuthAccount, User
from app.services import bot_intro, onboarding
from app.services.bot_dialog import handle_update

NOW = datetime(2026, 10, 1, 12, 0, 0)


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", "900000100")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        yield session


def _msg(text, tid="42"):
    return {"update_id": 1, "message": {"message_id": 5, "from": {"id": int(tid)},
                                        "chat": {"id": int(tid), "type": "private"}, "text": text}}


def _texts(out):
    return [o.payload.get("text", "") for o in out if o.method == "sendMessage"]


def test_start_dictor_asks_the_name(db):
    out = handle_update(db, _msg("/start dictor"), now=NOW)
    assert "Как вас зовут" in _texts(out)[0]
    assert bot_intro.intro_state(db, "42", now=NOW) == "fresh"


def test_plain_start_does_not_ask(db):
    out = handle_update(db, _msg("/start"), now=NOW)
    assert "пока не открыт" in _texts(out)[0] and db.query(BotIntro).count() == 0


def test_a_good_answer_makes_the_account_and_queues_the_followup(db):
    handle_update(db, _msg("/start dictor"), now=NOW)
    out = handle_update(db, _msg("ветрова ольга"), now=NOW + timedelta(minutes=1))
    assert "Готово, Ветрова Ольга" in _texts(out)[0]
    assert out[0].payload["reply_markup"]["keyboard"]
    assert [o.payload for o in out if o.method == "enqueue_intro"] == [{"telegram_user_id": "42"}]
    assert db.query(User).filter_by(login="tg_42").one().display_name == "Ветрова Ольга"


def test_a_bad_answer_asks_again_and_makes_nothing(db):
    handle_update(db, _msg("/start dictor"), now=NOW)
    out = handle_update(db, _msg("Оля"), now=NOW)
    assert "Не получилось разобрать" in _texts(out)[0] and db.query(User).count() == 0


def test_a_stale_question_sends_back_to_the_link(db):
    handle_update(db, _msg("/start dictor"), now=NOW - timedelta(days=2))
    out = handle_update(db, _msg("Ветрова Ольга"), now=NOW)
    assert "ссылку из приглашения" in _texts(out)[0] and db.query(User).count() == 0


def test_text_without_a_question_makes_no_account(db):
    out = handle_update(db, _msg("Ветрова Ольга"), now=NOW)
    assert "умею только" in _texts(out)[0] and db.query(User).count() == 0


def test_an_account_made_meanwhile_is_not_doubled(db):
    handle_update(db, _msg("/start dictor"), now=NOW)
    db.add(User(id="imp", login="tg_42", password_hash="x", display_name="Импорт Ольга", is_active="true"))
    db.commit()
    handle_update(db, _msg("Ветрова Ольга"), now=NOW)
    assert db.query(User).filter_by(login="tg_42").count() == 1


def test_a_switched_off_row_is_not_asked(db):
    db.add(TelegramAuthAccount(telegram_user_id="42", role="dictor", access_scope="full", display_name="", is_active="false"))
    db.commit()
    out = handle_update(db, _msg("/start dictor"), now=NOW)
    assert "пока не открыт" in _texts(out)[0] and db.query(BotIntro).count() == 0


def test_a_namesake_goes_to_the_owner(db):
    db.add(User(id="old", login="olga", password_hash="x", display_name="Ольга Ветрова", is_active="true"))
    db.commit()
    handle_update(db, _msg("/start dictor"), now=NOW)
    out = handle_update(db, _msg("Ветрова Ольга"), now=NOW)
    to_owner = [o.payload["text"] for o in out if o.method == "sendMessage" and o.payload["chat_id"] == "900000100"]
    assert len(to_owner) == 1 and "похожие учётки" in to_owner[0] and "Ольга Ветрова" in to_owner[0]


def _book_with_cast(db, actor="Ветрова Ольга", narrator=""):
    from app.models import BookBudget, Character, ScriptBook
    db.add(ScriptBook(id="b1", title="Крылья", display_title="Крылья", source_filename="x.docx", source_format="docx",
                      total_chars=0, author_sheets_x1000=0, chapter_count=1, has_chapters="true", status="processing"))
    db.add(Character(id="c1", book_id="b1", name="Дгарнин", aliases="", character_color="", actor_name=actor))
    if narrator:
        db.add(BookBudget(book_id="b1", narrator_actor_name=narrator))
    db.commit()


def test_a_cast_match_goes_to_the_owner(db):
    # Ревью 01.10: права диктора идут по имени — владелец должен знать, чьи роли человек получил.
    _book_with_cast(db)
    handle_update(db, _msg("/start dictor"), now=NOW)
    out = handle_update(db, _msg("Ольга Ветрова"), now=NOW)
    to_owner = [o.payload["text"] for o in out if o.method == "sendMessage" and o.payload["chat_id"] == "900000100"]
    assert len(to_owner) == 1 and "Дгарнин" in to_owner[0] and "Крылья" in to_owner[0]


def test_no_match_sends_nothing_to_the_owner(db):
    _book_with_cast(db, actor="Иванов Пётр")
    handle_update(db, _msg("/start dictor"), now=NOW)
    out = handle_update(db, _msg("Ветрова Ольга"), now=NOW)
    assert [o for o in out if o.method == "sendMessage" and o.payload["chat_id"] == "900000100"] == []
