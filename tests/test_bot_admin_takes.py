"""Администратор загружает чужую роль, даже когда у него есть собственные назначения."""
import pytest

from app.models import BotUpload, Character, TelegramAuthAccount, User, UserRole
from app.services.bot_dialog import handle_update
from app.services.take_format import REQUIRED
from tests.test_bot_agent_uploads import AGENT, NOW, _audio, _buttons, _press, _text, db
from tests.test_bot_agent_takes import _recording, _take_audio


def _admin(db, roles=("admin",)):
    _recording(db)
    db.query(UserRole).filter(UserRole.user_id == "u50").delete()
    for role in roles:
        db.add(UserRole(user_id="u50", role=role))
    db.get(User, "u50").display_name = "Администратор Студии"
    account = db.query(TelegramAuthAccount).filter_by(telegram_user_id=AGENT).one()
    account.role, account.display_name = roles[0], "Администратор Студии"
    db.add(Character(id="own", book_id="b1", name="Своя роль", actor_name="Администратор Студии", appears_in="1"))
    db.commit()


@pytest.mark.parametrize("roles", [("admin",), ("admin", "agent"), ("admin", "author"), ("admin", "dictor")])
def test_admin_named_file_uses_someone_elses_approved_role(db, roles):
    _admin(db, roles)
    out = handle_update(db, _take_audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert (row.purpose, row.state, row.role_name, row.actor_name) == \
        ("take", "queued", "Афыл", "Примерэклцлдзп Примергножжэщцб")
    assert [item.method for item in out] == ["sendMessage", "enqueue_bot_take"]


def test_admin_unknown_file_offers_own_and_other_roles(db):
    _admin(db)
    out = handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    assert _text(out) == "Это дубль или проба?"
    out = handle_update(db, _press(f"at:t:{row.id}"), now=NOW)
    buttons = _buttons(out)
    assert ("Афыл — Примерэклцлдзп Примергножжэщцб", f"at:r:{row.id}:c2") in buttons
    assert ("Своя роль — Администратор Студии", f"at:r:{row.id}:own") in buttons
    assert not any("Дракон" in name for name, _ in buttons)
    out = handle_update(db, _press(f"at:r:{row.id}:c2"), now=NOW)
    assert out[-1].method == "enqueue_bot_take"
    assert row.actor_name == "Примерэклцлдзп Примергножжэщцб" and row.character_id == "c2"
    assert not any(item.method.startswith("enqueue")
                   for item in handle_update(db, _press(f"at:r:{row.id}:c2"), now=NOW))


def test_admin_mp3_take_is_still_refused(db):
    _admin(db)
    out = handle_update(db, _take_audio("KP_Ch13_Afyl_OrtelOrvis.mp3"), now=NOW)
    assert _text(out) == REQUIRED and db.query(BotUpload).count() == 0


def test_admin_can_finish_the_mp3_audition_flow(db):
    _admin(db)
    out = handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert _text(out) == "Чья это проба?"
    handle_update(db, _press(f"ba:w:{row.id}:0"), now=NOW)
    out = handle_update(db, _press(f"ba:r:{row.id}:c1"), now=NOW)
    assert out[-1].method == "enqueue_bot_upload" and row.state == "queued"


def test_revoked_admin_cannot_use_old_upload_buttons(db):
    _admin(db)
    handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    db.query(UserRole).filter(UserRole.user_id == "u50").update({"role": "dictor"})
    db.commit()
    # Стартовая роль Telegram ещё «admin»: она не должна вернуть отозванное право.
    out = handle_update(db, _press(f"at:t:{row.id}"), now=NOW)
    assert [item.method for item in out] == ["answerCallbackQuery"]
    assert row.state == "choose" and row.purpose == "agentpick"


def test_author_is_not_given_admin_upload_rights(db):
    _admin(db, roles=("author", "agent"))
    out = handle_update(db, _take_audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert row.role_name == "Своя роль" and row.actor_name == "Администратор Студии"
    assert row.role_name != "Афыл"


def test_dictor_cannot_use_someone_elses_admin_buttons(db):
    _admin(db)
    handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    out = handle_update(db, _press(f"at:t:{row.id}", tid="41"), now=NOW)
    assert [item.method for item in out] == ["answerCallbackQuery"]
    assert row.state == "choose"
