"""Кнопки сроков в боте (пакет B, 05.10): нажатия диктора и решения владельца."""
import calendar
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AuditLog, Character, RoleDeadline, ScriptBook, ScriptChapter, TelegramAuthAccount, User, UserRole
from app.services import bot_deadlines, onboarding
from app.services.bot_dialog import handle_update

NOW = datetime(2026, 10, 5, 9, 0, 0)
DUE = NOW + timedelta(hours=27)          # 6 октября, 15:00 МСК
OWNER, AGENT = "11", "70"


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(bot_deadlines.settings, "owner_telegram_id", OWNER)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Карта Приозерья", display_title="Карта Приозерья",
                               source_filename="x", source_format="docx", total_chars=0, author_sheets_x1000=0,
                               chapter_count=1, has_chapters="true", status="processing"))
        session.add(ScriptChapter(id="b1-1", book_id="b1", chapter_index=1, chapter_title="Глава 1"))
        people = [("u11", OWNER, "Фернос Фарита", "admin"), ("u41", "41", "Ветрова Ольга", "dictor"),
                  ("u42", "42", "Иванов Пётр", "dictor"), ("u70", AGENT, "Инга Агент", "agent"),
                  ("u80", "80", "Варени Вискон", "author")]
        for uid, tid, name, role in people:
            session.add(User(id=uid, login=f"tg_{tid}", password_hash="x", display_name=name, is_active="true"))
            session.add(UserRole(user_id=uid, role=role))
            session.add(TelegramAuthAccount(telegram_user_id=tid, role=role, access_scope="full", display_name=name,
                                            is_active="true", user_id=uid))
        session.add(Character(id="c1", book_id="b1", name="Дракон", actor_name="Ветрова Ольга?", appears_in="1"))
        session.add(Character(id="c2", book_id="b1", name="Убув", actor_name="Ветрова Ольга", appears_in="1"))
        session.add(RoleDeadline(id="d1", character_id="c1", book_id="b1", actor_name="Ветрова Ольга",
                                 kind="audition", due_at=DUE, created_at=NOW))
        session.add(RoleDeadline(id="d2", character_id="c2", book_id="b1", actor_name="Ветрова Ольга",
                                 kind="role", due_at=NOW + timedelta(days=60), created_at=NOW))
        session.commit()
        yield session


def _press(data, tid="41", message_id=9):
    return {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": int(tid)}, "data": data,
                                               "message": {"message_id": message_id,
                                                           "chat": {"id": int(tid), "type": "private"}}}}


def _to(out, chat):
    return [o.payload for o in out if o.method == "sendMessage" and str(o.payload["chat_id"]) == str(chat)]


def _dx(action, at=NOW):
    return f"dx:{action}:d2:{calendar.timegm(at.timetuple())}"


def _data(payload):
    return [b["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for b in row]


def test_take_marks_the_deadline_and_tells_owner_and_agents_once(db):
    out = handle_update(db, _press("dl:y:c1"), now=NOW)
    assert db.get(RoleDeadline, "d1").accepted_at == NOW
    assert "The audition is due 6 October, 15:00 MSK" in _to(out, "41")[0]["text"]
    owner = _to(out, OWNER)[0]["text"]
    assert "Ветрова Ольга взяла" not in owner and "✅ Ветрова Ольга — audition «Дракон» («Карта Приозерья») accepted" in owner
    assert _to(out, AGENT) and "reply_markup" not in _to(out, AGENT)[0]
    again = handle_update(db, _press("dl:y:c1"), now=NOW + timedelta(minutes=1))
    assert not _to(again, OWNER) and db.get(RoleDeadline, "d1").accepted_at == NOW
    assert db.query(AuditLog).filter_by(action="bot_accept").count() == 1


def test_someone_else_cannot_press_and_stale_invites_change_nothing(db):
    out = handle_update(db, _press("dl:y:c1", tid="42"), now=NOW)
    assert "for another person" in _to(out, "42")[0]["text"] and db.get(RoleDeadline, "d1").accepted_at is None
    db.get(RoleDeadline, "d1").closed_at, db.get(RoleDeadline, "d1").close_reason = NOW, "replaced"
    db.commit()
    out = handle_update(db, _press("dl:y:c1"), now=NOW)
    assert "no longer current" in _to(out, "41")[0]["text"]
    out = handle_update(db, _press("dl:n:c2"), now=NOW)       # «Не смогу» — только у пробы
    assert "no longer current" in _to(out, "41")[0]["text"] and db.get(RoleDeadline, "d2").closed_at is None


def test_decline_closes_the_audition_and_asks_the_owner(db):
    out = handle_update(db, _press("dl:n:c1"), now=NOW)
    row = db.get(RoleDeadline, "d1")
    assert (row.closed_at, row.close_reason, row.declined_at) == (NOW, "declined", NOW)
    assert "Thank you for letting us know" in _to(out, "41")[0]["text"]
    owner = _to(out, OWNER)[0]
    assert "✖️ Ветрова Ольга cannot record — audition «Дракон» («Карта Приозерья»)" in owner["text"]
    assert _data(owner) == ["dd:r:c1", "dd:k:c1"]
    assert "reply_markup" not in _to(out, AGENT)[0]


def test_time_button_offers_days_by_kind(db):
    audition = _to(handle_update(db, _press("dl:t:c1"), now=NOW), "41")[0]
    role = _to(handle_update(db, _press("dl:t:c2"), now=NOW), "41")[0]
    assert _data(audition) == ["dl:e1:c1", "dl:e2:c1"] and _data(role) == ["dl:e7:c2", "dl:e14:c2"]


def test_an_extension_request_goes_to_the_owner_once(db):
    out = handle_update(db, _press("dl:e7:c2"), now=NOW)
    row = db.get(RoleDeadline, "d2")
    assert (row.extend_request_days, row.extend_request_at) == (7, NOW)
    assert "Request sent" in _to(out, "41")[0]["text"]
    owner = _to(out, OWNER)[0]
    assert "⏳ Ветрова Ольга asks to extend role «Убув» («Карта Приозерья») by 7 days" in owner["text"]
    stamp = calendar.timegm(NOW.timetuple())
    assert _data(owner) == [f"dx:y:d2:{stamp}", f"dx:n:d2:{stamp}"]
    again = handle_update(db, _press("dl:e14:c2"), now=NOW)
    assert "already been sent" in _to(again, "41")[0]["text"] and db.get(RoleDeadline, "d2").extend_request_days == 7


def test_days_outside_the_kind_are_refused(db):
    out = handle_update(db, _press("dl:e7:c1"), now=NOW)      # проба: только 1 или 2
    assert "expired" in _to(out, "41")[0]["text"] and db.get(RoleDeadline, "d1").extend_request_days is None


def _decline(db):
    handle_update(db, _press("dl:n:c1"), now=NOW)
    db.commit()


def test_owner_removes_the_actor_through_a_vote(db):
    _decline(db)
    out = handle_update(db, _press("dd:r:c1", tid=OWNER, message_id=33), now=NOW)
    assert db.get(Character, "c1").actor_name == ""
    assert "Unassigned: role «Дракон» is available" in _to(out, OWNER)[0]["text"]
    assert any(o.method == "editMessageReplyMarkup" and o.payload["message_id"] == 33 for o in out)
    assert db.query(AuditLog).filter_by(action="bot_decline_remove").count() == 1


def test_the_authors_vote_outweighs_the_owner(db):
    from app.services.role_votes import cast_role_vote
    cast_role_vote(db, character=db.get(Character, "c1"), voter_uid="u80", voter_name="Варени Вискон",
                   weight=2, actor_name="Ветрова Ольга?")
    db.commit()
    _decline(db)
    out = handle_update(db, _press("dd:r:c1", tid=OWNER), now=NOW)
    assert db.get(Character, "c1").actor_name == "Ветрова Ольга?"
    assert "Not reassigned: the deciding vote belongs to Варени Вискон" in _to(out, OWNER)[0]["text"]


def test_remove_does_nothing_when_the_role_already_moved_on(db):
    _decline(db)
    db.get(Character, "c1").actor_name = "Иванов Пётр?"
    db.commit()
    out = handle_update(db, _press("dd:r:c1", tid=OWNER), now=NOW)
    assert db.get(Character, "c1").actor_name == "Иванов Пётр?"
    assert "The role is already assigned to Иванов Пётр" in _to(out, OWNER)[0]["text"]


def test_keep_and_agents_cannot_decide(db):
    _decline(db)
    assert [o.method for o in handle_update(db, _press("dd:r:c1", tid=AGENT), now=NOW)] == ["answerCallbackQuery"]
    out = handle_update(db, _press("dd:k:c1", tid=OWNER), now=NOW)
    assert "assignment has been kept" in _to(out, OWNER)[0]["text"] and db.get(Character, "c1").actor_name == "Ветрова Ольга?"


def test_owner_extends_once_and_the_dictor_hears(db):
    handle_update(db, _press("dl:e7:c2"), now=NOW)
    db.commit()
    before = db.get(RoleDeadline, "d2").due_at
    out = handle_update(db, _press(_dx("y"), tid=OWNER), now=NOW)
    row = db.get(RoleDeadline, "d2")
    assert row.due_at == before + timedelta(days=7) and row.extend_request_days is None
    assert row.extended_by == "Фернос Фарита"
    assert "Deadline extended" in _to(out, "41")[0]["text"] and "Extended until" in _to(out, OWNER)[0]["text"]
    again = handle_update(db, _press(_dx("y"), tid=OWNER), now=NOW)
    assert db.get(RoleDeadline, "d2").due_at == before + timedelta(days=7)
    assert "no longer current" in _to(again, OWNER)[0]["text"]


def test_owner_refuses_and_only_admin_decides(db):
    handle_update(db, _press("dl:e7:c2"), now=NOW)
    db.commit()
    assert [o.method for o in handle_update(db, _press(_dx("y"), tid="80"), now=NOW)] == ["answerCallbackQuery"]
    out = handle_update(db, _press(_dx("n"), tid=OWNER), now=NOW)
    assert db.get(RoleDeadline, "d2").extend_request_days is None
    assert "Could not extend the deadline" in _to(out, "41")[0]["text"] and "contact the studio" in _to(out, "41")[0]["text"]


def test_the_request_reads_in_the_accusative(db):
    """Прогон на копии прода: «просит продлить проба «Ао»» — нужен винительный падеж."""
    out = handle_update(db, _press("dl:e1:c1"), now=NOW)
    assert "asks to extend audition «Дракон» («Карта Приозерья») by 1 day" in _to(out, OWNER)[0]["text"]


def test_an_expired_audition_says_the_deadline_has_passed(db):
    """Прогон на копии прода: «Ждём пробу до 3 октября» 5 октября — неправда. Срок вышел —
    так и говорим и подсказываем кнопку продления."""
    late = DUE + timedelta(hours=1)
    taken = _to(handle_update(db, _press("dl:y:c1"), now=late), "41")[0]["text"]
    assert "audition deadline has passed" in taken and "Need more time" in taken
    asked = _to(handle_update(db, _press("dl:t:c1"), now=late), "41")[0]["text"]
    assert asked.startswith("Deadline passed 6 October, 15:00 MSK")


def test_a_fresh_invitation_after_decline_opens_a_new_audition(db):
    """Ревью: «Не смогу» блокировало пробу навсегда — даже после того, как владелец снял
    человека и позвал снова (диктор мог ошибиться кнопкой). Отказ держит, только пока каст
    не менялся: сняли или отдали другому — новое приглашение открывает новый срок."""
    from app.services import role_deadlines
    _decline(db)
    ch = db.get(Character, "c1")
    ch.actor_name = ""
    role_deadlines.sync_book(db, "b1", now=NOW)
    ch.actor_name = "Ветрова Ольга?"
    role_deadlines.sync_book(db, "b1", now=NOW + timedelta(minutes=5))
    row = role_deadlines.open_deadline(db, "c1")
    assert row is not None and row.kind == "audition" and row.accepted_at is None


def test_a_site_extension_clears_the_bot_request_and_old_buttons_go_stale(db):
    """Ревью: продление на сайте не гасило запрос из бота — диктор упирался в «уже
    отправлен», а старая кнопка «Продлить на 2» позже добавляла дни сверх решения."""
    from app.services import role_deadlines
    out = handle_update(db, _press("dl:e7:c2"), now=NOW)
    old_button = _data(_to(out, OWNER)[0])[0]
    role_deadlines.extend(db, "d2", days=14, by="Фернос Фарита", now=NOW)
    db.commit()
    assert db.get(RoleDeadline, "d2").extend_request_days is None
    due = db.get(RoleDeadline, "d2").due_at
    handle_update(db, _press("dl:e7:c2"), now=NOW + timedelta(minutes=1))   # новый запрос
    db.commit()
    stale = handle_update(db, _press(old_button, tid=OWNER), now=NOW + timedelta(minutes=2))
    assert db.get(RoleDeadline, "d2").due_at == due and "no longer current" in _to(stale, OWNER)[0]["text"]


def test_remove_reports_who_holds_the_role_when_votes_hand_it_to_someone_else(db):
    """Ревью: пересчёт голосов мог отдать роль третьему, а бот отвечал «Снято: свободна»,
    и новый исполнитель не получал письма."""
    from app.services.role_votes import cast_role_vote
    ch = db.get(Character, "c1")
    cast_role_vote(db, character=ch, voter_uid="u80", voter_name="Варени Вискон", weight=2,
                   actor_name="Иванов Пётр?")
    db.commit()
    ch.actor_name = "Ветрова Ольга?"   # профиль автора пишет имя мимо голосов
    db.commit()
    _decline(db)
    letters = []
    import app.services.role_approval as ra
    orig = ra.send_telegram_message
    ra.send_telegram_message = lambda db, text, chat_ids=None, direct=False, **kw: letters.append(chat_ids) or 1
    try:
        out = handle_update(db, _press("dd:r:c1", tid=OWNER), now=NOW)
    finally:
        ra.send_telegram_message = orig
    assert db.get(Character, "c1").actor_name == "Иванов Пётр?"
    assert "The role is now assigned to Иванов Пётр" in _to(out, OWNER)[0]["text"] and ["42"] in letters


def test_outweighed_reply_has_no_gendered_words(db):
    from app.services.role_votes import cast_role_vote
    cast_role_vote(db, character=db.get(Character, "c1"), voter_uid="u80", voter_name="Варени Вискон",
                   weight=2, actor_name="Ветрова Ольга?")
    db.commit()
    _decline(db)
    text = _to(handle_update(db, _press("dd:r:c1", tid=OWNER), now=NOW), OWNER)[0]["text"]
    assert "за ним" not in text and "that vote decides" in text
