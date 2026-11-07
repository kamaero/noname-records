"""Демо, пересланные владельцем боту: узнавание, вопрос об имени на пачку, кнопки (проект Б)."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import (
    BotCommentWait, BotDemoIntake, BotDemoItem, BotUpload, DictorLink, DictorProfile, TelegramAuthAccount, User,
    UserRole,
)
from app.services import bot_uploads, onboarding
from app.services.bot_dialog import handle_update

NOW = datetime(2026, 10, 23, 9, 0, 0)
OWNER = "900000100"


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", OWNER)
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


def _user(session, uid, name, *, roles=("dictor",), tid="", active="true", login=""):
    session.add(User(id=uid, login=login or uid, password_hash="x", display_name=name, is_active=active))
    for role in roles:
        session.add(UserRole(user_id=uid, role=role))
    if tid:
        session.add(TelegramAuthAccount(telegram_user_id=tid, role=roles[0], access_scope="full", display_name=name,
                                        is_active="true", user_id=uid))


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        _user(session, "uo", "Фернос Фарита", roles=("admin",), tid=OWNER)
        _user(session, "u41", "Ветрова Ольга", tid="41")
        session.commit()
        yield session


def _fwd(origin_id=777, *, mid=100, first="Пётр", last="Сидоров", username="petr", hidden="", sender=OWNER,
         **extra):
    if hidden:
        origin = {"type": "hidden_user", "date": 1, "sender_user_name": hidden}
    else:
        origin = {"type": "user", "date": 1, "sender_user": {"id": origin_id, "is_bot": False, "first_name": first,
                                                               "last_name": last, "username": username}}
    message = {"message_id": mid, "from": {"id": int(sender)}, "chat": {"id": int(sender), "type": "private"},
               "forward_origin": origin}
    if not any(k in extra for k in ("audio", "text", "caption", "voice")):
        extra["audio"] = {"file_id": f"F{mid}", "file_unique_id": f"U{mid}", "mime_type": "audio/mpeg",
                          "file_name": "demo.mp3", "file_size": 3_000_000}
    message.update(extra)
    return {"update_id": mid, "message": message}


def _text(text, *, reply_to=None, mid=500):
    message = {"message_id": mid, "from": {"id": int(OWNER)}, "chat": {"id": int(OWNER), "type": "private"},
               "text": text}
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": 1, "text": reply_to}
    return {"update_id": mid, "message": message}


def _press(data, mid=9):
    return {"update_id": 900, "callback_query": {"id": "cb", "from": {"id": int(OWNER)}, "data": data,
                                                 "message": {"message_id": mid,
                                                             "chat": {"id": int(OWNER), "type": "private"}}}}


def _texts(out):
    return [o.payload.get("text", "") for o in out if o.method == "sendMessage"]


def _buttons(out):
    rows = [o.payload["reply_markup"]["inline_keyboard"] for o in out
            if o.method == "sendMessage" and "reply_markup" in o.payload]
    return [button for markup in rows for row in markup for button in row]


def _enqueued(out):
    return [o.payload["id"] for o in out if o.method == "enqueue_bot_demo"]


def _question(out):
    return next(t for t in _texts(out) if "#" in t)


def test_known_dictor_added_silently(db):
    out = handle_update(db, _fwd(41, first="Ольга", last="Ветрова"), now=NOW)
    item = db.query(BotDemoItem).one()
    assert item.state == "queued" and _enqueued(out) == [item.id]
    assert any("Ветрова Ольга" in t for t in _texts(out))
    assert db.query(BotDemoIntake).one().state == "resolved"


def test_forward_from_non_owner_is_not_demo(db):
    handle_update(db, _fwd(777, sender="41"), now=NOW)
    assert db.query(BotDemoItem).count() == 0


def test_owner_forwarded_audio_never_becomes_audition(db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("пересланное владельцем ушло в пробы")
    monkeypatch.setattr(bot_uploads, "receive", boom)
    handle_update(db, _fwd(41), now=NOW)
    assert db.query(BotUpload).count() == 0


def test_new_sender_asked_once_per_pack(db):
    first = handle_update(db, _fwd(777, mid=100), now=NOW)
    second = handle_update(db, _fwd(777, mid=101), now=NOW)
    third = handle_update(db, _fwd(777, mid=102, text="Привет, баритон"), now=NOW)
    assert "Сидоров" in _question(first) or "Пётр" in _question(first)
    assert _texts(second) == [] and _texts(third) == []
    assert [i.state for i in db.query(BotDemoItem).order_by(BotDemoItem.source_message_id)] == ["waiting"] * 3
    assert db.query(BotDemoIntake).count() == 1


def test_pick_telegram_name_creates_dictor_like_import(db):
    handle_update(db, _fwd(777, mid=100), now=NOW)
    handle_update(db, _fwd(777, mid=101), now=NOW)
    intake = db.query(BotDemoIntake).one()
    out = handle_update(db, _press(f"dm:n:{intake.id}"), now=NOW)
    user = db.query(User).filter_by(login="tg_777").one()
    assert user.display_name == "Пётр Сидоров"
    access = db.query(TelegramAuthAccount).filter_by(telegram_user_id="777").one()
    assert (access.user_id, access.is_active, access.access_scope) == (user.id, "true", "full")
    items = db.query(BotDemoItem).all()
    assert {i.state for i in items} == {"queued"} and sorted(_enqueued(out)) == sorted(i.id for i in items)
    assert db.get(DictorProfile, user.id).telegram_username == "petr"


def test_double_press_creates_one_dictor(db):
    handle_update(db, _fwd(777), now=NOW)
    intake = db.query(BotDemoIntake).one()
    handle_update(db, _press(f"dm:n:{intake.id}"), now=NOW)
    again = handle_update(db, _press(f"dm:n:{intake.id}"), now=NOW)
    assert db.query(User).filter_by(login="tg_777").count() == 1
    assert _enqueued(again) == []


def test_same_message_twice_is_one_item(db):
    handle_update(db, _fwd(41, mid=100), now=NOW)
    again = handle_update(db, _fwd(41, mid=100), now=NOW)
    assert db.query(BotDemoItem).count() == 1 and _enqueued(again) == []


def test_name_taken_offers_namesake(db):
    _user(db, "u50", "Иванова Ольга")
    db.commit()
    handle_update(db, _fwd(777), now=NOW)
    intake = db.query(BotDemoIntake).one()
    out = handle_update(db, _text("Ольга Иванова"), now=NOW)
    data = [b["callback_data"] for b in _buttons(out)]
    assert f"dm:p:{intake.id}:u50" in data and f"dm:o:{intake.id}" in data
    assert db.get(BotDemoIntake, intake.id).state == "ask"
    handle_update(db, _press(f"dm:o:{intake.id}"), now=NOW)
    made = db.query(User).filter_by(display_name="Ольга Иванова 2").one()
    assert db.get(BotDemoIntake, intake.id).user_id == made.id


def test_pick_existing_card_attaches_telegram_only_when_free(db):
    _user(db, "u60", "Сидоров Пётр")
    _user(db, "u61", "Сидоров Павел", tid="61")
    db.commit()
    handle_update(db, _fwd(777, mid=100), now=NOW)
    first = db.query(BotDemoIntake).one()
    handle_update(db, _press(f"dm:p:{first.id}:u60"), now=NOW)
    assert db.query(TelegramAuthAccount).filter_by(telegram_user_id="777").one().user_id == "u60"

    handle_update(db, _fwd(888, mid=200, first="Павел"), now=NOW)
    second = db.query(BotDemoIntake).filter_by(sender_key="tg:888").one()
    out = handle_update(db, _press(f"dm:p:{second.id}:u61"), now=NOW)
    assert db.query(TelegramAuthAccount).filter_by(telegram_user_id="888").count() == 0
    assert any("Telegram was not linked" in t for t in _texts(out))
    assert db.get(BotDemoIntake, second.id).user_id == "u61"


def test_foreign_id_not_revived(db):
    _user(db, "u90", "Старый Аккаунт", tid="900", active="false")
    db.commit()
    out = handle_update(db, _fwd(900, first="Анна", last="Новая"), now=NOW)
    assert "disabled" in _question(out)
    intake = db.query(BotDemoIntake).one()
    handle_update(db, _press(f"dm:n:{intake.id}"), now=NOW)
    made = db.get(User, db.get(BotDemoIntake, intake.id).user_id)
    assert made.display_name == "Анна Новая" and made.login != "tg_900"
    assert db.get(User, "u90").is_active == "false"
    assert db.query(TelegramAuthAccount).filter_by(telegram_user_id="900").one().user_id == "u90"


def test_hidden_sender_always_asked(db):
    out = handle_update(db, _fwd(hidden="Мария Скрытая"), now=NOW)
    assert "Мария Скрытая" in _question(out)
    intake = db.query(BotDemoIntake).one()
    handle_update(db, _press(f"dm:n:{intake.id}"), now=NOW)
    made = db.get(User, db.get(BotDemoIntake, intake.id).user_id)
    assert made.display_name == "Мария Скрытая"
    assert db.query(TelegramAuthAccount).filter_by(user_id=made.id).count() == 0


def test_two_open_intakes_need_reply(db):
    one = handle_update(db, _fwd(777, mid=100), now=NOW)
    two = handle_update(db, _fwd(888, mid=200, first="Павел", last="Орлов"), now=NOW)
    hint = handle_update(db, _text("Орлов Павел"), now=NOW)
    assert any("open questions: 2" in t for t in _texts(hint))
    handle_update(db, _text("Орлов Павел", reply_to=_question(two)), now=NOW)
    second = db.query(BotDemoIntake).filter_by(sender_key="tg:888").one()
    first = db.query(BotDemoIntake).filter_by(sender_key="tg:777").one()
    assert second.state == "resolved" and db.get(User, second.user_id).display_name == "Орлов Павел"
    assert first.state == "ask" and _question(one)


def test_comment_wait_wins_over_plain_text(db):
    handle_update(db, _fwd(777), now=NOW)
    db.add(BotCommentWait(tid=OWNER, audio_id="gone", created_at=NOW))
    db.commit()
    handle_update(db, _text("Пётр Сидоров"), now=NOW)
    assert db.query(BotDemoIntake).one().state == "ask"


def test_cancel_closes_pack(db):
    handle_update(db, _fwd(777, mid=100), now=NOW)
    handle_update(db, _fwd(777, mid=101), now=NOW)
    intake = db.query(BotDemoIntake).one()
    out = handle_update(db, _press(f"dm:x:{intake.id}"), now=NOW)
    assert db.get(BotDemoIntake, intake.id).state == "cancelled"
    assert {i.state for i in db.query(BotDemoItem)} == {"cancelled"}
    assert any("Cancelled" in t for t in _texts(out))


def test_text_goes_to_note(db):
    db.add(DictorProfile(user_id="u41", note="Старое"))
    db.commit()
    handle_update(db, _fwd(41, caption="баритон, опыт 5 лет"), now=NOW)
    note = db.get(DictorProfile, "u41").note
    assert note.startswith("Старое\n") and "23.10 from Telegram: баритон, опыт 5 лет" in note


def test_portfolio_link_saved_in_dialog(db):
    url = "https://example.com/portfolio"
    out = handle_update(db, _fwd(41, text=f"Мои работы {url}",
                                 entities=[{"type": "url", "offset": 11, "length": len(url)}]), now=NOW)
    assert db.query(DictorLink).filter_by(user_id="u41", url=url).count() == 1
    link_item = db.query(BotDemoItem).filter_by(kind="link").one()
    assert link_item.state == "link_saved" and link_item.id not in _enqueued(out)


def test_channel_forward_refused(db):
    update = _fwd(41)
    update["message"]["forward_origin"] = {"type": "channel", "date": 1, "chat": {"id": -100, "title": "К"},
                                           "message_id": 3}
    out = handle_update(db, update, now=NOW)
    assert any("not from a person" in t for t in _texts(out)) and db.query(BotDemoItem).count() == 0


def test_reply_to_closed_question_never_lands_elsewhere(db):
    """Ревью I-1: реплай на отменённый вопрос о Петре завёл Марию под именем Петра."""
    old = handle_update(db, _fwd(777, mid=100), now=NOW)
    first = db.query(BotDemoIntake).one()
    handle_update(db, _press(f"dm:x:{first.id}"), now=NOW)
    handle_update(db, _fwd(888, mid=200, first="Мария", last="Белова"), now=NOW)
    out = handle_update(db, _text("Сидоров Пётр", reply_to=_question(old)), now=NOW)
    assert any("is closed" in t for t in _texts(out))
    assert db.query(BotDemoIntake).filter_by(sender_key="tg:888").one().state == "ask"
    assert db.query(User).filter_by(display_name="Сидоров Пётр").count() == 0


def test_repeat_after_answer_asks_nothing(db):
    """Ревью I-2: повтор пересылки после ответа снова спрашивал имя и оставлял пустой приём."""
    handle_update(db, _fwd(hidden="Аня К", mid=100), now=NOW)
    intake = db.query(BotDemoIntake).one()
    handle_update(db, _text("Кузнецова Анна"), now=NOW)
    again = handle_update(db, _fwd(hidden="Аня К", mid=100), now=NOW)
    assert _texts(again) == []
    assert db.query(BotDemoIntake).filter_by(state="ask").count() == 0 and db.get(BotDemoIntake, intake.id)


def test_failed_attach_leaves_no_half_account(db):
    """Ревью I-4: telegram_taken после create_user оставлял учётку tg_<id> без Telegram навсегда."""
    db.add(TelegramAuthAccount(telegram_user_id="777", role="dictor", access_scope="full", display_name="?",
                               is_active="true", user_id="gone-user"))
    db.commit()
    handle_update(db, _fwd(777), now=NOW)
    intake = db.query(BotDemoIntake).one()
    handle_update(db, _press(f"dm:n:{intake.id}"), now=NOW)
    db.commit()
    assert db.query(User).filter_by(login="tg_777").count() == 0
    made = db.get(User, db.get(BotDemoIntake, intake.id).user_id)
    assert made.display_name == "Пётр Сидоров"
    assert db.query(TelegramAuthAccount).filter_by(telegram_user_id="777").one().user_id == "gone-user"


def test_create_failure_rolls_back_the_account(db, monkeypatch):
    """Ревью I-4: сбой привязки после create_user не должен оставить полузаведённую учётку."""
    from app.services import user_admin

    def taken(db, **kw):
        raise user_admin.UserAdminError("telegram_taken")
    monkeypatch.setattr(user_admin, "attach_telegram", taken)
    handle_update(db, _fwd(777), now=NOW)
    intake = db.query(BotDemoIntake).one()
    out = handle_update(db, _press(f"dm:n:{intake.id}"), now=NOW)
    db.commit()
    assert db.query(User).filter_by(login="tg_777").count() == 0
    assert db.get(BotDemoIntake, intake.id).state == "ask"
    assert any("telegram_taken" in t for t in _texts(out))


def test_stale_question_ignores_plain_text(db):
    """Отложенное M-3: забытый вопрос принимал любую фразу из 2–4 слов как имя диктора."""
    from datetime import timedelta

    asked = handle_update(db, _fwd(777), now=NOW)
    later = NOW + timedelta(hours=2)
    out = handle_update(db, _text("Завтра запись отменяется"), now=later)
    assert db.query(BotDemoIntake).one().state == "ask"
    assert not any("Завёл" in t for t in _texts(out))
    handle_update(db, _text("Сидоров Пётр", reply_to=_question(asked)), now=later)
    assert db.query(BotDemoIntake).one().state == "resolved"


def test_late_forward_keeps_question_fresh(db):
    from datetime import timedelta

    handle_update(db, _fwd(777, mid=100), now=NOW)
    handle_update(db, _fwd(777, mid=101), now=NOW + timedelta(minutes=90))
    handle_update(db, _text("Сидоров Пётр"), now=NOW + timedelta(minutes=100))
    assert db.query(BotDemoIntake).one().state == "resolved"


def test_pick_existing_reply_reads_naturally(db):
    _user(db, "u60", "Сидоров Пётр")
    db.commit()
    handle_update(db, _fwd(777), now=NOW)
    intake = db.query(BotDemoIntake).one()
    out = handle_update(db, _press(f"dm:p:{intake.id}:u60"), now=NOW)
    assert any(t.startswith("Adding to profile: Сидоров Пётр.") for t in _texts(out))
