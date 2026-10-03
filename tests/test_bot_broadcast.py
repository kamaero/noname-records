"""Рассылка из бота: админ выбирает книгу (или всех), пишет текст, подтверждает (01.10)."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    AuditLog, BotBroadcast, BookBudget, DictorAssignment, ScriptBook, TelegramAuthAccount, User, UserRole,
)
from app.services import bot_broadcast, onboarding
from app.services.bot_dialog import BROADCAST_BUTTON, handle_update

NOW = datetime(2026, 10, 1, 12, 0, 0)


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


def _person(db, uid, name, *, role="dictor", tid=""):
    db.add(User(id=uid, login=uid, password_hash="x", display_name=name, is_active="true"))
    db.add(UserRole(user_id=uid, role=role))
    if tid:
        db.add(TelegramAuthAccount(telegram_user_id=tid, role=role, access_scope="full", display_name=name,
                                   is_active="true", user_id=uid))


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        for bid, title in (("b1", "Сказки волшебников. Книга 3"), ("b2", "Крылья Полумрака")):
            session.add(ScriptBook(id=bid, title=title, display_title=title, source_filename="x", source_format="docx",
                                   total_chars=0, author_sheets_x1000=0, chapter_count=1, has_chapters="true",
                                   status="processing"))
        _person(session, "admin", "Тимур Садыков", role="admin", tid="900000100")
        _person(session, "u1", "Ветрова Ольга", tid="41")
        _person(session, "u2", "Иванов Пётр", tid="42")
        _person(session, "u3", "Без Телеграма")
        _person(session, "u4", "Сидорова Анна", tid="44")
        session.add(DictorAssignment(user_id="u1", book_id="b1", character_id="c1", role_name="Куйбу"))
        session.add(DictorAssignment(user_id="u1", book_id="b1", character_id="c2", role_name="Егбод"))
        session.add(DictorAssignment(user_id="u2", book_id="b1", character_id="c3", role_name="Астрид", state="proposed"))
        session.add(DictorAssignment(user_id="u3", book_id="b1", character_id="", role_name="Рассказчик"))
        session.add(DictorAssignment(user_id="u4", book_id="b2", character_id="c9", role_name="Погтда"))
        session.commit()
        yield session


def _msg(text, tid="900000100"):
    return {"update_id": 1, "message": {"message_id": 1, "from": {"id": int(tid)},
                                        "chat": {"id": int(tid), "type": "private"}, "text": text}}


def _press(data, tid="900000100"):
    return {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": int(tid)}, "data": data,
                                               "message": {"message_id": 9, "chat": {"id": int(tid), "type": "private"}}}}


def _buttons(out):
    return [b for o in out for row in o.payload.get("reply_markup", {}).get("inline_keyboard", []) for b in row]


def _texts(out):
    return [o.payload.get("text", "") for o in out if o.method == "sendMessage"]


def _pick(out, label):
    return next(b["callback_data"] for b in _buttons(out) if label in b["text"])


def test_the_admin_has_the_broadcast_button(db):
    out = handle_update(db, _msg("/start"), now=NOW)
    assert out[0].payload["reply_markup"]["keyboard"] == [[{"text": BROADCAST_BUTTON}]]


def test_the_book_flow_reaches_only_approved_cast_and_narrator(db):
    out = handle_update(db, _msg(BROADCAST_BUTTON), now=NOW)
    labels = [b["text"] for b in _buttons(out)]
    assert "Сказки волшебников. Книга 3" in labels and "Всем дикторам" in labels
    out = handle_update(db, _press(_pick(out, "Книга 3")), now=NOW)
    out = handle_update(db, _press(_pick(out, "Утверждённым")), now=NOW)
    assert "2 человека" in _texts(out)[0]  # Ольга (две роли — один раз) и рассказчик
    out = handle_update(db, _msg("Главы 1–10 готовы к записи!"), now=NOW)
    preview = "\n".join(_texts(out))
    assert "📢 Сказки волшебников. Книга 3" in preview and "Главы 1–10 готовы к записи!" in preview
    assert "без Telegram: Без Телеграма" in preview
    out = handle_update(db, _press(_pick(out, "Отправить")), now=NOW)
    job = [o for o in out if o.method == "broadcast"]
    assert len(job) == 1

    sent = []
    summary = bot_broadcast.deliver(db, job[0].payload["id"], send=lambda chat, text: sent.append((chat, text)) or True,
                                    now=NOW)
    db.commit()
    assert [chat for chat, _ in sent] == ["41"]
    assert sent[0][1].startswith("📢 Сказки волшебников. Книга 3\n\nГлавы 1–10")
    assert "Ушло: 1" in summary and "Без Телеграма" in summary
    assert db.query(AuditLog).filter_by(action="bot_broadcast").count() == 1


def test_proposed_ones_are_added_on_request(db):
    out = handle_update(db, _msg(BROADCAST_BUTTON), now=NOW)
    out = handle_update(db, _press(_pick(out, "Книга 3")), now=NOW)
    out = handle_update(db, _press(_pick(out, "позванным на пробу")), now=NOW)
    assert "3 человека" in _texts(out)[0]


def test_everyone_means_every_dictor(db):
    out = handle_update(db, _msg(BROADCAST_BUTTON), now=NOW)
    out = handle_update(db, _press(_pick(out, "Всем дикторам")), now=NOW)
    assert "4 человека" in _texts(out)[0]
    handle_update(db, _msg("Всем привет"), now=NOW)
    bc = db.query(BotBroadcast).one()
    chats = sorted(chat for _, chat in bot_broadcast.recipients(db, bc) if chat)
    assert chats == ["41", "42", "44"]


def test_pressing_send_twice_sends_once(db):
    out = handle_update(db, _msg(BROADCAST_BUTTON), now=NOW)
    out = handle_update(db, _press(_pick(out, "Всем дикторам")), now=NOW)
    out = handle_update(db, _msg("Текст"), now=NOW)
    send = _pick(out, "Отправить")
    first = handle_update(db, _press(send), now=NOW)
    second = handle_update(db, _press(send), now=NOW)
    assert len([o for o in first if o.method == "broadcast"]) == 1
    assert [o for o in second if o.method == "broadcast"] == []


def test_cancel_stops_the_broadcast(db):
    out = handle_update(db, _msg(BROADCAST_BUTTON), now=NOW)
    out = handle_update(db, _press(_pick(out, "Отмена")), now=NOW)
    assert db.query(BotBroadcast).one().state == "cancelled"
    out = handle_update(db, _msg("Просто текст"), now=NOW)
    assert db.query(BotBroadcast).one().text == ""


def test_a_dictor_can_neither_start_nor_press(db):
    out = handle_update(db, _msg(BROADCAST_BUTTON, tid="41"), now=NOW)
    assert db.query(BotBroadcast).count() == 0
    admin = handle_update(db, _msg(BROADCAST_BUTTON), now=NOW)
    out = handle_update(db, _press(_pick(admin, "Всем дикторам"), tid="41"), now=NOW)
    assert db.query(BotBroadcast).one().state == "choose"


def test_a_broadcast_left_for_an_hour_is_forgotten(db):
    out = handle_update(db, _msg(BROADCAST_BUTTON), now=NOW)
    handle_update(db, _press(_pick(out, "Всем дикторам")), now=NOW)
    handle_update(db, _msg("Поздний текст"), now=NOW + timedelta(hours=2))
    assert db.query(BotBroadcast).one().text == ""


# --- ревью 01.10 ---

def _ready(db, text="Текст"):
    out = handle_update(db, _msg(BROADCAST_BUTTON), now=NOW)
    out = handle_update(db, _press(_pick(out, "Всем дикторам")), now=NOW)
    out = handle_update(db, _msg(text), now=NOW)
    return out


def test_two_presses_from_stale_sessions_send_once(tmp_path):
    # Telegram может прислать два нажатия параллельно: вторая сессия ещё видит «confirm».
    engine = create_engine(f"sqlite:///{tmp_path / 'bc.db'}")
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, autoflush=False)
    with make() as db:
        _person(db, "admin", "Тимур Садыков", role="admin", tid="900000100")
        _person(db, "u1", "Ветрова Ольга", tid="41")
        db.commit()
        out = _ready(db)
        db.commit()
        send = _pick(out, "Отправить")
    a, b = make(), make()
    held = (a.query(BotBroadcast).one(), b.query(BotBroadcast).one())  # обе сессии прочли «confirm» и держат его
    first = handle_update(a, _press(send), now=NOW)
    a.commit()
    second = handle_update(b, _press(send), now=NOW)
    b.commit()
    emitted = [o for o in first + second if o.method == "broadcast"]
    assert len(emitted) == 1 and held
    a.close(), b.close()


def test_a_crash_midway_leaves_a_record_of_who_got_it(db):
    _ready(db)
    bc = db.query(BotBroadcast).one()
    bc.state = "sent"
    db.commit()
    got = []

    class Crash(BaseException):
        pass

    def send(chat, text):
        if got:
            raise Crash()
        got.append(chat)
        return True
    with pytest.raises(Crash):
        bot_broadcast.deliver(db, bc.id, send=send, now=NOW)
    db.rollback()
    audit = db.query(AuditLog).filter_by(action="bot_broadcast").one()
    import json
    assert json.loads(db.get(BotBroadcast, bc.id).failed_json)["delivered"] == ["Ветрова Ольга"]
    assert "Иванов Пётр" in json.loads(audit.payload_json)["recipients"]


def test_a_failed_send_is_not_blamed_on_start(db):
    _ready(db)
    bc = db.query(BotBroadcast).one()
    summary = bot_broadcast.deliver(db, bc.id, send=lambda chat, text: False, now=NOW)
    assert "Не дошло" in summary and "Start" not in summary


def test_too_long_text_is_refused_not_cut(db):
    out = _ready(db, text="а" * 5000)
    assert "длинн" in " ".join(_texts(out)) and db.query(BotBroadcast).one().state == "text"


def test_the_old_password_button_is_not_taken_as_text(db):
    from app.services.bot_dialog import BUTTON_TEXT
    out = handle_update(db, _msg(BROADCAST_BUTTON), now=NOW)
    handle_update(db, _press(_pick(out, "Всем дикторам")), now=NOW)
    handle_update(db, _msg(BUTTON_TEXT), now=NOW)
    assert db.query(BotBroadcast).one().text == ""


def test_an_unlinked_tg_account_still_counts_as_telegram(db):
    _person(db, "tg_77", "Новый Диктор")
    db.add(TelegramAuthAccount(telegram_user_id="77", role="dictor", access_scope="full", display_name="Новый Диктор",
                               is_active="true"))
    db.commit()
    bc = BotBroadcast(admin_tid="900000100", scope="all", created_at=NOW)
    db.add(bc)
    db.commit()
    assert ("Новый Диктор", "77") in bot_broadcast.recipients(db, bc)
