"""Бот: кнопки статуса и доступ к обзору студии (пакет A, 05.10)."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import TelegramAuthAccount, User, UserRole
from app.services import bot_status, onboarding
from app.services.bot_dialog import (
    BROADCAST_BUTTON, BUTTON_TEXT, HOW_BUTTON, REMIND_BUTTON, STATUS_BUTTON, STUDIO_BUTTON, handle_update,
)

NOW = datetime(2026, 10, 5, 9, 0, 0)


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(bot_status, "chat_for", lambda db, actor: "")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        yield session


def _person(db, tid, *, roles, tg_role=None):
    db.add(User(id=f"u{tid}", login=f"tg_{tid}", password_hash="x", display_name=f"Человек {tid}", is_active="true"))
    for role in roles:
        db.add(UserRole(user_id=f"u{tid}", role=role))
    db.add(TelegramAuthAccount(telegram_user_id=tid, role=tg_role or roles[0], access_scope="full",
                               display_name=f"Человек {tid}", is_active="true", user_id=f"u{tid}"))
    db.commit()


def _msg(text, tid):
    return {"update_id": 1, "message": {"message_id": 1, "from": {"id": int(tid)},
                                        "chat": {"id": int(tid), "type": "private"}, "text": text}}


def _keyboard(out):
    return [[b["text"] for b in row] for row in out[0].payload["reply_markup"]["keyboard"]]


def test_a_dictor_gets_the_status_button(db):
    _person(db, "41", roles=("dictor",))
    out = handle_update(db, _msg("/start", "41"), now=NOW)
    assert _keyboard(out) == [[BUTTON_TEXT, HOW_BUTTON], [STATUS_BUTTON]]


def test_the_status_button_shows_the_status(db):
    _person(db, "41", roles=("dictor",))
    out = handle_update(db, _msg(STATUS_BUTTON, "41"), now=NOW)
    assert "Ваш статус в" in out[0].payload["text"]


@pytest.mark.parametrize("roles,tg_role", [(("author",), None), (("agent",), None), (("dictor",), "agent")])
def test_agent_and_author_also_get_the_studio_button(db, roles, tg_role):
    _person(db, "50", roles=roles, tg_role=tg_role)
    out = handle_update(db, _msg("/start", "50"), now=NOW)
    assert _keyboard(out) == [[BUTTON_TEXT, HOW_BUTTON], [STATUS_BUTTON], [STUDIO_BUTTON]]


def test_an_admin_gets_studio_and_status_next_to_broadcast(db):
    _person(db, "11", roles=("admin",))
    out = handle_update(db, _msg("/start", "11"), now=NOW)
    assert _keyboard(out) == [[BROADCAST_BUTTON, REMIND_BUTTON], [STUDIO_BUTTON, STATUS_BUTTON]]


def _press(data, tid):
    return {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": int(tid)}, "data": data,
                                               "message": {"message_id": 9, "chat": {"id": int(tid), "type": "private"}}}}


@pytest.mark.parametrize("text", ["/studiostatus", "/studiostatus@nonamerecords_bot", STUDIO_BUTTON])
def test_staff_get_the_overview(db, text):
    _person(db, "50", roles=("author",))
    out = handle_update(db, _msg(text, "50"), now=NOW)
    assert "Студия —" in out[0].payload["text"] and out[0].payload["reply_markup"]["inline_keyboard"]


def test_a_dictor_gets_no_overview(db):
    _person(db, "41", roles=("dictor",))
    out = handle_update(db, _msg("/studiostatus", "41"), now=NOW)
    assert "Студия —" not in out[0].payload["text"] and "/status" in out[0].payload["text"]
    pressed = handle_update(db, _press("ss:p", "41"), now=NOW)
    assert [o.method for o in pressed] == ["answerCallbackQuery"]


def test_a_section_button_sends_the_section(db):
    _person(db, "11", roles=("admin",))
    out = handle_update(db, _press("ss:p", "11"), now=NOW)
    assert out[0].method == "answerCallbackQuery" and "Пробы ждут решения" in out[1].payload["text"]


@pytest.mark.parametrize("text", ["/dictor Человек 41", "/диктор Человек 41"])
def test_staff_can_look_up_a_dictor(db, text):
    _person(db, "11", roles=("admin",))
    _person(db, "41", roles=("dictor",))
    out = handle_update(db, _msg(text, "11"), now=NOW)
    assert "Человек 41" in out[0].payload["text"] and "<code>tg_41</code>" in out[0].payload["text"]


def test_a_dictor_cannot_look_up_others(db):
    _person(db, "41", roles=("dictor",))
    _person(db, "42", roles=("dictor",))
    out = handle_update(db, _msg("/dictor Человек 42", "41"), now=NOW)
    assert "tg_42" not in out[0].payload["text"]
    assert [o.method for o in handle_update(db, _press("dc:t:42", "41"), now=NOW)] == ["answerCallbackQuery"]


def test_only_the_agent_role_is_taken_from_the_telegram_row(db):
    """Ревью 05.10: роль в записи телеграм-доступа — стартовая, экран учёток её не
    переписывает. Разжалованный «author» в ней не должен открывать обзор; берём оттуда
    только `agent`, как `agent_chat_ids`."""
    _person(db, "60", roles=("dictor",), tg_role="author")
    out = handle_update(db, _msg("/studiostatus", "60"), now=NOW)
    assert "Студия —" not in out[0].payload["text"]
