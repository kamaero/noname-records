"""Агент присылает боту пробу за подопечного диктора (29.10, просьба агента).

Подопечные — все, кого предлагали в «Касте» (голоса) или поставили на пробу («Имя?»), в
неозвученных книгах. Проба — на любую роль, без приглашения: агент ведёт актёров и грузит
за них по роду занятий, как на сайте.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, BotUpload, Character, ScriptBook, TelegramAuthAccount, User, UserRole
from app.models import RoleVote
from app.services import bot_agent_uploads, bot_uploads, onboarding
from app.services.bot_dialog import handle_update

NOW = datetime(2026, 10, 29, 19, 0, 0)
AGENT = "50"


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


def _book(session, book_id, title, voiced=None):
    session.add(ScriptBook(id=book_id, title=title, display_title=title, source_filename="x", source_format="docx",
                           total_chars=0, author_sheets_x1000=0, chapter_count=1, has_chapters="true",
                           status="processing", voiced_at=voiced))


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        _book(session, "b1", "Карта Приозерья")
        _book(session, "b9", "Старая книга", voiced=NOW)
        session.add(Character(id="c1", book_id="b1", name="Дракон", actor_name="Ветрова Ольга?", appears_in="1"))
        session.add(Character(id="c2", book_id="b1", name="Убув", actor_name="", appears_in="1"))
        session.add(Character(id="c9", book_id="b9", name="Король", actor_name="Сидоров Олег?", appears_in="1"))
        session.add(RoleVote(id="v1", character_id="c2", voter_uid="owner", voter_name="Хозяин",
                             actor_name="Иванов Пётр", weight=1, created_at=NOW, updated_at=NOW))
        session.add(RoleVote(id="v9", character_id="c9", voter_uid="owner", voter_name="Хозяин",
                             actor_name="Сидоров Олег", weight=1, created_at=NOW, updated_at=NOW))
        session.add(User(id="u50", login="inga", password_hash="x", display_name="Урвена Ульвен", is_active="true"))
        session.add(UserRole(user_id="u50", role="agent"))
        session.add(TelegramAuthAccount(telegram_user_id=AGENT, role="agent", access_scope="full",
                                        display_name="Урвена Ульвен", is_active="true", user_id="u50"))
        session.add(User(id="u41", login="tg_41", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
        session.add(UserRole(user_id="u41", role="dictor"))
        session.add(TelegramAuthAccount(telegram_user_id="41", role="dictor", access_scope="full",
                                        display_name="Ветрова Ольга", is_active="true", user_id="u41"))
        session.commit()
        yield session


def _audio(tid=AGENT):
    return {"update_id": 1, "message": {
        "message_id": 7, "from": {"id": int(tid)}, "chat": {"id": int(tid), "type": "private"},
        "audio": {"file_id": "FILE", "file_unique_id": "U1", "mime_type": "audio/mpeg",
                  "file_name": "proba.mp3", "file_size": 3_000_000, "duration": 40}}}


def _press(data, tid=AGENT):
    return {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": int(tid)}, "data": data,
                                               "message": {"message_id": 9, "chat": {"id": int(tid), "type": "private"}}}}


def _buttons(out):
    message = next(item for item in out if item.method == "sendMessage")
    return [(b["text"], b["callback_data"]) for row in message.payload["reply_markup"]["inline_keyboard"] for b in row]


def _text(out):
    return next(item for item in out if item.method == "sendMessage").payload["text"]


def test_the_agent_is_asked_whose_audition_it_is(db):
    out = handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert (row.purpose, row.state, row.telegram_user_id) == ("agent", "choose", AGENT)
    assert _text(out) == 'Whose audition is this?'
    # озвученная книга не в счёт: Сидорова предлагали только туда
    assert _buttons(out) == [("Ветрова Ольга", f"ba:w:{row.id}:0"), ("Иванов Пётр", f"ba:w:{row.id}:1"),
                             ("Cancel", f"ba:x:{row.id}")]


def test_the_ward_brings_the_roles_he_was_proposed_for(db):
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    out = handle_update(db, _press(f"ba:w:{row.id}:0"), now=NOW)
    assert db.get(BotUpload, row.id).actor_name == "Ветрова Ольга"
    assert "Ветрова Ольга" in _text(out)
    assert _buttons(out) == [("«Дракон» («Карта Приозерья»)", f"ba:r:{row.id}:c1"),
                             ('Another role…', f"ba:o:{row.id}"), ("Cancel", f"ba:x:{row.id}")]


def test_another_role_lists_the_whole_cast_of_the_only_open_book(db):
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    handle_update(db, _press(f"ba:w:{row.id}:0"), now=NOW)
    out = handle_update(db, _press(f"ba:o:{row.id}"), now=NOW)
    assert _buttons(out) == [("Дракон", f"ba:r:{row.id}:c1"), ("Убув", f"ba:r:{row.id}:c2"),
                             ("Cancel", f"ba:x:{row.id}")]


def test_choosing_the_role_queues_the_audition_under_the_ward(db):
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    handle_update(db, _press(f"ba:w:{row.id}:1"), now=NOW)
    out = handle_update(db, _press(f"ba:r:{row.id}:c2"), now=NOW)
    row = db.get(BotUpload, row.id)
    assert (row.state, row.character_id, row.actor_name) == ("queued", "c2", "Иванов Пётр")
    assert [item.method for item in out if item.method != "answerCallbackQuery"] == ["sendMessage", "enqueue_bot_upload"]
    assert "«Убув»" in _text(out) and "Иванов Пётр" in _text(out)
    assert handle_update(db, _press(f"ba:r:{row.id}:c2"), now=NOW)[-1].payload["text"] == \
        'This audition has already been accepted or cancelled.'


def test_a_long_list_of_wards_is_paged(db):
    for number in range(30):
        db.add(RoleVote(id=f"x{number}", character_id="c2", voter_uid=f"voter{number}", voter_name="",
                        actor_name=f"Актёр{number:02d} Имя", weight=1, created_at=NOW, updated_at=NOW))
    db.commit()
    out = handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    first = _buttons(out)
    assert len([b for b in first if b[1].startswith("ba:w:")]) == bot_agent_uploads.PAGE
    assert ('Next ›', f"ba:wp:{row.id}:1") in first
    second = _buttons(handle_update(db, _press(f"ba:wp:{row.id}:1"), now=NOW))
    assert ('‹ Back', f"ba:wp:{row.id}:0") in second
    assert ("Ветрова Ольга", f"ba:w:{row.id}:{bot_agent_uploads.PAGE}") in second or \
        any(b[1] == f"ba:w:{row.id}:{bot_agent_uploads.PAGE}" for b in second)


def test_somebody_elses_press_is_ignored(db):
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert [item.method for item in handle_update(db, _press(f"ba:w:{row.id}:0", tid="41"), now=NOW)] == \
        ["answerCallbackQuery"]
    assert db.get(BotUpload, row.id).actor_name == ""


def test_a_dictor_without_invitation_still_hears_the_old_rule(db):
    out = handle_update(db, _audio(tid="41"), now=NOW)
    assert "by invitation" in _text(out)


def _fake_store(db, **kw):
    item = AudioFile(book_code=kw["book_code"], original_filename=kw["original_filename"], stored_key="k/a",
                     mime_type=kw["mime_type"], size_bytes=len(kw["source"].read()), chapter=kw["chapter"],
                     role=kw["role"], actor_name=kw["actor_name"], kind=kw["kind"], codec="mp3")
    db.add(item)
    db.flush()
    return item


def _fetch(file_id):
    import contextlib
    import io

    @contextlib.contextmanager
    def opened():
        yield io.BytesIO(b"ID3")
    return opened()


def _queued(db, character_id="c2"):
    row = BotUpload(id="up1", telegram_user_id=AGENT, file_id="FILE", kind="audio", mime_type="audio/mpeg",
                    file_name="p.mp3", purpose="agent", character_id=character_id, actor_name="Иванов Пётр",
                    state="queued", created_at=NOW, updated_at=NOW)
    db.add(row)
    db.commit()
    return row


def test_store_needs_no_invitation_for_the_agent(db, monkeypatch):
    _queued(db)
    cards, texts = [], []
    monkeypatch.setattr(bot_uploads, "send_card", lambda db, audio_id, **kw: cards.append(audio_id) or 2)
    result = bot_uploads.store(db, "up1", fetch=_fetch, send_text=lambda chat, text: texts.append((chat, text)) or True,
                               send_audio=lambda chat, **kw: True, store_file=_fake_store, now=NOW)
    audio = db.query(AudioFile).one()
    assert result == "stored"
    assert (audio.kind, audio.role, audio.actor_name) == ("audition", "Убув", "Иванов Пётр")
    assert texts == [(AGENT, "Audition received — «Убув», Иванов Пётр.")]
    assert cards == [audio.id]


def test_store_refuses_a_role_in_a_voiced_book(db, monkeypatch):
    _queued(db, character_id="c9")
    monkeypatch.setattr(bot_uploads, "send_card", lambda *a, **k: pytest.fail("карточки быть не должно"))
    texts = []
    result = bot_uploads.store(db, "up1", fetch=_fetch, send_text=lambda chat, text: texts.append(text) or True,
                               send_audio=lambda chat, **kw: True, store_file=_fake_store, now=NOW)
    assert result == "failed" and db.query(AudioFile).count() == 0
    assert "role" in texts[0]
