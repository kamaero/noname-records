import calendar
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AuditLog, TelegramAuthAccount, User, UserRole
from app.services import bot_dialog, onboarding
from app.services.bot_dialog import BUTTON_TEXT, handle_update
from app.services.login_identity import TELEGRAM_PASSWORD_HASH
from app.services.passwords import hash_password, verify_password

NOW = datetime(2026, 10, 1, 12, 0, 0)
TS = calendar.timegm(NOW.timetuple())


@pytest.fixture(autouse=True)
def _studio_site(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "app_base_url", "https://studio.example")


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        yield session


def _account(db, tid="42", *, roles=("dictor",), password_hash=TELEGRAM_PASSWORD_HASH, linked=True, active="true"):
    db.add(User(id="u1", login="olga", password_hash=password_hash, display_name="Ольга Ветрова", is_active=active))
    for i, role in enumerate(roles):
        db.add(UserRole(id=f"r{i}", user_id="u1", role=role))
    db.add(TelegramAuthAccount(telegram_user_id=tid, role=roles[0], access_scope="full", display_name="Ольга Ветрова",
                               is_active="true", user_id="u1" if linked else ""))
    db.commit()


def _msg(text, tid="42", chat_type="private"):
    return {"update_id": 1, "message": {"message_id": 5, "from": {"id": int(tid)},
                                        "chat": {"id": int(tid), "type": chat_type}, "text": text}}


def _press(data, tid="42"):
    return {"update_id": 2, "callback_query": {"id": "cb1", "from": {"id": int(tid)}, "data": data,
                                               "message": {"message_id": 6, "chat": {"id": int(tid), "type": "private"}}}}


def _texts(out):
    return [o.payload.get("text", "") for o in out if o.method == "sendMessage"]


def _has_button(out):
    return any(o.payload.get("reply_markup", {}).get("keyboard") for o in out)


def test_start_for_a_dictor_greets_shows_the_button_and_marks_reachable(db):
    _account(db)
    out = handle_update(db, _msg("/start dictor"), now=NOW)
    assert "Вы на связи с Noname Records" in _texts(out)[0] and _has_button(out)
    assert onboarding.bot_reachable("42") is True


def test_start_without_an_account_says_access_is_not_open(db):
    out = handle_update(db, _msg("/start"), now=NOW)
    assert "Доступ к сайту пока не открыт" in _texts(out)[0] and not _has_button(out)


def test_start_for_an_admin_has_no_password_button_but_the_broadcast_one(db):
    # С 01.10 у админа — кнопка рассылки; кнопки пароля у него по-прежнему нет.
    from app.services.bot_dialog import BROADCAST_BUTTON
    _account(db, roles=("admin", "dictor"))
    out = handle_update(db, _msg("/start"), now=NOW)
    assert "Вы на связи с Noname Records" in _texts(out)[0]
    assert out[0].payload["reply_markup"]["keyboard"] == [[{"text": BROADCAST_BUTTON}]]


def test_button_without_a_password_issues_one(db):
    _account(db)
    out = handle_update(db, _msg(BUTTON_TEXT), now=NOW)
    text = _texts(out)[0]
    assert "olga" in text and "studio.example/app/login" in text
    password = text.split("<code>")[2].split("</code>")[0]
    assert verify_password(password, db.get(User, "u1").password_hash)
    assert out[0].payload["parse_mode"] == "HTML" and out[0].payload["chat_id"] == 42


def test_button_with_a_password_asks_first_and_changes_nothing(db):
    _account(db, password_hash=hash_password("old-password"))
    out = handle_update(db, _msg(BUTTON_TEXT), now=NOW)
    assert "уже есть пароль" in _texts(out)[0]
    button = out[0].payload["reply_markup"]["inline_keyboard"][0][0]
    assert button["callback_data"] == f"pwreset:{TS}"
    assert verify_password("old-password", db.get(User, "u1").password_hash)


def test_a_fresh_confirmation_issues_a_new_password(db):
    _account(db, password_hash=hash_password("old-password"))
    out = handle_update(db, _press(f"pwreset:{TS - 60}"), now=NOW)
    assert out[0].method == "answerCallbackQuery"
    assert "<code>olga</code>" in _texts(out)[0]
    assert not verify_password("old-password", db.get(User, "u1").password_hash)


@pytest.mark.parametrize("data", [f"pwreset:{TS - 601}", "pwreset:abc", "pwreset:", f"pwreset:{TS + 3600}"])
def test_a_stale_or_broken_confirmation_issues_nothing(db, data):
    _account(db, password_hash=hash_password("old-password"))
    out = handle_update(db, _press(data), now=NOW)
    assert "устарела" in _texts(out)[0]
    assert verify_password("old-password", db.get(User, "u1").password_hash)


def test_a_confirmation_after_the_account_was_switched_off_issues_nothing(db):
    _account(db, password_hash=hash_password("old-password"), active="false")
    out = handle_update(db, _press(f"pwreset:{TS}"), now=NOW)
    assert "пока не открыт" in _texts(out)[0]
    assert verify_password("old-password", db.get(User, "u1").password_hash)


def test_an_admin_gets_no_password(db):
    _account(db, roles=("admin",))
    out = handle_update(db, _msg(BUTTON_TEXT), now=NOW)
    assert "только на сайте" in _texts(out)[0]
    assert db.get(User, "u1").password_hash == TELEGRAM_PASSWORD_HASH


def test_the_sixth_issue_in_an_hour_is_refused(db):
    _account(db)
    for minutes in range(5):
        db.add(AuditLog(user_id="u1", entity_type="user", entity_id="u1", action="password_issued_by_bot",
                        payload_json="{}", created_at=NOW - timedelta(minutes=minutes)))
    db.commit()
    out = handle_update(db, _press(f"pwreset:{TS}"), now=NOW)
    assert "Слишком часто" in _texts(out)[0]


def test_an_unlinked_whitelist_row_is_not_matched_to_a_namesake(db):
    # Ревью 01.10: сверка по имени в боте отдала бы чужую учётку и сбросила её пароль —
    # хозяин остался бы снаружи. Бот находит учётку только по привязке или tg_<id>.
    _account(db, linked=False, password_hash=hash_password("old-password"))
    out = handle_update(db, _msg(BUTTON_TEXT), now=NOW)
    assert "пока не открыт" in _texts(out)[0]
    assert verify_password("old-password", db.get(User, "u1").password_hash)
    assert db.query(TelegramAuthAccount).one().user_id == ""
    assert db.query(User).count() == 1


def test_start_creates_no_account(db):
    db.add(TelegramAuthAccount(telegram_user_id="42", role="dictor", access_scope="full",
                               display_name="Новый Диктор", is_active="true"))
    db.commit()
    out = handle_update(db, _msg("/start"), now=NOW)
    assert "пока не открыт" in _texts(out)[0]
    assert db.query(User).count() == 0


def test_an_unlinked_row_finds_the_tg_login_the_import_made(db):
    db.add(User(id="u9", login="tg_42", password_hash=TELEGRAM_PASSWORD_HASH, display_name="Ольга", is_active="true"))
    db.add(UserRole(id="r9", user_id="u9", role="dictor"))
    db.add(TelegramAuthAccount(telegram_user_id="42", role="dictor", access_scope="full",
                               display_name="Ольга", is_active="true"))
    db.commit()
    out = handle_update(db, _msg(BUTTON_TEXT), now=NOW)
    assert "<code>tg_42</code>" in _texts(out)[0]


def test_other_text_explains_what_the_bot_does(db):
    _account(db)
    out = handle_update(db, _msg("привет"), now=NOW)
    assert "умею только" in _texts(out)[0] and _has_button(out)


@pytest.mark.parametrize("update", [
    _msg("/start", chat_type="group"),
    {"update_id": 3, "edited_message": {"text": "x"}},
    {"update_id": 4},
])
def test_groups_and_other_updates_are_ignored(db, update):
    assert handle_update(db, update, now=NOW) == []
