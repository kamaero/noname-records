"""Дубль агента не должен попадать в пробы и терять утверждённого актёра."""
from datetime import timedelta

import pytest

from tests.test_bot_agent_uploads import AGENT, NOW, _audio, _buttons, _press, _text, db
from app.models import BotUpload, Character, TelegramAuthAccount, UserRole
from app.services.bot_dialog import handle_update
from app.services.studio_settings import set_active_book
from app.services.take_format import REQUIRED


def _recording(db, *, several=False):
    role = db.get(Character, "c2")
    role.name, role.actor_name = "Афыл", "Ортель Орвис"
    if several:
        db.add(Character(id="c3", book_id="b1", name="Второй", actor_name="Другой Диктор", appears_in="1"))
    set_active_book(db, "b1", updated_by="owner")
    db.commit()


def _take_audio(name="KP_Ch13_Afyl_OrtelOrvis.wav", *, tid=AGENT):
    update = _audio(tid)
    update["message"]["audio"].update(file_name=name, mime_type="audio/wav")
    return update


@pytest.mark.parametrize("extension", ["wav", "WAV", "flac"])
def test_named_take_uses_the_approved_actor_without_questions(db, extension):
    _recording(db)
    out = handle_update(db, _take_audio(f"KP_Ch13_Afyl_OrtelOrvis.{extension}"), now=NOW)
    row = db.query(BotUpload).one()
    assert (row.purpose, row.state, row.character_id, row.actor_name, row.role_name, row.book_id) == \
        ("take", "queued", "c2", "Ортель Орвис", "Афыл", "b1")
    assert [item.method for item in out] == ["sendMessage", "enqueue_bot_take"]
    assert "распознаю" in _text(out) and "reply_markup" not in out[0].payload
    repeat = handle_update(db, _take_audio(f"KP_Ch13_Afyl_OrtelOrvis.{extension}"), now=NOW)
    assert "уже принят" in _text(repeat) and db.query(BotUpload).count() == 1


def test_mp3_named_as_a_take_is_rejected_without_audition_questions(db):
    _recording(db)
    out = handle_update(db, _take_audio("KP_Ch13_Afyl_OrtelOrvis.mp3"), now=NOW)
    assert _text(out) == REQUIRED and db.query(BotUpload).count() == 0
    assert not any(item.method.startswith("enqueue") for item in out)


def test_unknown_filename_asks_only_kind_then_uses_the_approved_role(db):
    _recording(db)
    out = handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    assert _text(out) == "Это дубль или проба?" and row.purpose == "agentpick"
    assert ("Дубль", f"at:t:{row.id}") in _buttons(out)
    selected = handle_update(db, _press(f"at:t:{row.id}"), now=NOW)
    assert [item.method for item in selected] == ["answerCallbackQuery", "sendMessage", "enqueue_bot_take"]
    assert (row.purpose, row.state, row.actor_name) == ("take", "queued", "Ортель Орвис")
    repeated = handle_update(db, _press(f"at:t:{row.id}"), now=NOW)
    assert not any(item.method.startswith("enqueue") for item in repeated)


def test_unknown_mp3_can_be_an_audition_but_not_a_take(db):
    _recording(db)
    handle_update(db, _take_audio("Untitled.mp3"), now=NOW)
    row = db.query(BotUpload).one()
    assert _text(handle_update(db, _press(f"at:t:{row.id}"), now=NOW)) == REQUIRED
    assert (row.purpose, row.state) == ("agentpick", "choose")
    out = handle_update(db, _press(f"at:a:{row.id}"), now=NOW)
    assert _text(out) == "Чья это проба?" and row.purpose == "agent"
    handle_update(db, _press(f"ba:w:{row.id}:0"), now=NOW)
    queued = handle_update(db, _press(f"ba:r:{row.id}:c1"), now=NOW)
    assert row.state == "queued" and row.purpose == "agent"
    assert queued[-1].method == "enqueue_bot_upload"


def test_explicit_audition_filename_keeps_the_old_mp3_flow(db):
    _recording(db)
    out = handle_update(db, _take_audio("KP_Afyl_OrtelOrvis_proba.mp3"), now=NOW)
    assert _text(out) == "Чья это проба?"
    assert db.query(BotUpload).one().purpose == "agent"


def test_choose_role_without_choosing_actor(db):
    _recording(db, several=True)
    handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    out = handle_update(db, _press(f"at:t:{row.id}"), now=NOW)
    assert ("Афыл — Ортель Орвис", f"at:r:{row.id}:c2") in _buttons(out)
    assert not any("Дракон" in label for label, _ in _buttons(out))
    queued = handle_update(db, _press(f"at:r:{row.id}:c2"), now=NOW)
    assert row.state == "queued" and row.actor_name == "Ортель Орвис"
    assert queued[-1].method == "enqueue_bot_take"


def test_callbacks_recheck_agent_access_and_approved_role(db):
    _recording(db, several=True)
    handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    foreign = handle_update(db, _press(f"at:t:{row.id}", tid="41"), now=NOW)
    assert [item.method for item in foreign] == ["answerCallbackQuery"]
    assert row.purpose == "agentpick"
    handle_update(db, _press(f"at:t:{row.id}"), now=NOW)
    db.get(Character, "c2").actor_name = "Ортель Орвис?"
    db.commit()
    out = handle_update(db, _press(f"at:r:{row.id}:c2"), now=NOW)
    assert "больше не утверждена" in _text(out) and row.state == "choose"
    db.query(UserRole).filter(UserRole.user_id == "u50").delete()
    db.query(TelegramAuthAccount).filter(TelegramAuthAccount.telegram_user_id == AGENT).update({"role": "dictor"})
    db.commit()
    assert [item.method for item in handle_update(db, _press(f"at:r:{row.id}:c3"), now=NOW)] == \
        ["answerCallbackQuery"]
    assert row.state == "choose"


def test_choice_expires_and_cannot_follow_another_active_book(db):
    _recording(db)
    handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    out = handle_update(db, _press(f"at:t:{row.id}"), now=NOW + timedelta(days=2))
    assert "устарел" in _text(out) and row.state == "choose"
    set_active_book(db, None, updated_by="owner")
    db.commit()
    out = handle_update(db, _press(f"at:t:{row.id}"), now=NOW)
    assert "не записывает ни одну книгу" in _text(out) and row.state == "choose"


def test_cancel_before_selecting_kind(db):
    _recording(db)
    handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    out = handle_update(db, _press(f"at:x:{row.id}"), now=NOW)
    assert _text(out) == "Отменено." and row.state == "cancelled"


def test_no_active_book_does_not_turn_an_unknown_file_into_an_audition(db):
    out = handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    assert _text(out) == "Это дубль или проба?"
    out = handle_update(db, _press(f"at:t:{row.id}"), now=NOW)
    assert "не записывает ни одну книгу" in _text(out) and row.purpose == "agentpick"


def test_role_choices_are_paged_and_callback_data_fits_telegram(db):
    import uuid

    _recording(db)
    for i in range(25):
        db.add(Character(id=str(uuid.uuid4()), book_id="b1", name=f"Роль {i:02d}",
                         actor_name="Другой Диктор", appears_in="1"))
    db.commit()
    handle_update(db, _take_audio("Untitled.wav"), now=NOW)
    row = db.query(BotUpload).one()
    out = handle_update(db, _press(f"at:t:{row.id}"), now=NOW)
    buttons = _buttons(out)
    assert len([data for _, data in buttons if data.startswith("at:r:")]) == 20
    assert all(len(data.encode()) <= 64 for _, data in buttons)
    assert ("Дальше ›", f"at:p:{row.id}:1") in buttons
    out = handle_update(db, _press(f"at:p:{row.id}:1"), now=NOW)
    assert ("Афыл — Ортель Орвис", f"at:r:{row.id}:c2") in _buttons(out)
    assert ("‹ Назад", f"at:p:{row.id}:0") in _buttons(out)
