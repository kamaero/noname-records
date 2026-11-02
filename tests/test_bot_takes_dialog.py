"""Приём дубля ботом (спека 2026-10-28-bot-takes, часть 3): учёт, выбор роли, очередь."""
import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import BotUpload, Character, RoleDeadline, ScriptBook, TelegramAuthAccount, User, UserRole
from app.services import bot_status, bot_takes, onboarding
from app.services.bot_dialog import handle_update
from app.services.studio_settings import set_active_book

NOW = datetime(2026, 10, 6, 9, 0, 0)
CHATS = {"Ветрова Ольга": "41", "Иванов Пётр": "42"}
QUEUED = "Принял файл, распознаю и разложу по главам — пришлю отчёт через несколько минут."


@pytest.fixture(autouse=True)
def _clean_reach_cache():
    onboarding._cache.clear()
    yield
    onboarding._cache.clear()


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(bot_status, "chat_for", lambda db, actor: CHATS.get(actor, ""))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Карта Приозерья", display_title="Карта Приозерья",
                               source_filename="x", source_format="docx", total_chars=0, author_sheets_x1000=0,
                               chapter_count=1, has_chapters="true", status="processing"))
        for tid, name in (("41", "Ветрова Ольга"), ("42", "Иванов Пётр")):
            session.add(User(id=f"u{tid}", login=f"tg_{tid}", password_hash="x", display_name=name, is_active="true"))
            session.add(UserRole(user_id=f"u{tid}", role="dictor"))
            session.add(TelegramAuthAccount(telegram_user_id=tid, role="dictor", access_scope="full",
                                            display_name=name, is_active="true", user_id=f"u{tid}"))
        session.flush()
        set_active_book(session, "b1", updated_by="t")
        session.commit()
        yield session


def _role(db, cid, name, actor="Ветрова Ольга"):
    db.add(Character(id=cid, book_id="b1", name=name, actor_name=actor, appears_in="1"))
    db.commit()


def _invite(db, cid="c9", name="Дракон"):
    _role(db, cid, name, actor="Ветрова Ольга?")
    db.add(RoleDeadline(id=f"d{cid}", character_id=cid, book_id="b1", actor_name="Ветрова Ольга",
                        kind="audition", due_at=NOW + timedelta(hours=20), created_at=NOW))
    db.commit()


def _audio(tid="41", name="zapis.wav", unique="U1"):
    message = {"message_id": 7, "from": {"id": int(tid)}, "chat": {"id": int(tid), "type": "private"},
               "audio": {"file_id": "F" * 80, "file_unique_id": unique, "mime_type": "audio/wav",
                         "file_name": name, "file_size": 3_000_000, "duration": 40}}
    return {"update_id": 1, "message": message}


def _press(data, tid="41"):
    return {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": int(tid)}, "data": data,
                                               "message": {"message_id": 9, "chat": {"id": int(tid), "type": "private"}}}}


def _buttons(payload):
    return [(b["text"], b["callback_data"]) for row in payload["reply_markup"]["inline_keyboard"] for b in row]


def _enqueued(out, method="enqueue_bot_take"):
    return [o.payload for o in out if o.method == method]


def test_single_role_queues_at_once(db):
    _role(db, "c1", "Лымэли")
    out = handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert (row.purpose, row.state, row.character_id, row.role_name, row.actor_name, row.book_id) == \
        ("take", "queued", "c1", "Лымэли", "Ветрова Ольга", "b1")
    assert out[0].payload["text"] == QUEUED
    assert _enqueued(out) == [{"id": row.id}]


def test_role_from_filename_among_several(db):
    for cid, name in (("c1", "Сэкле"), ("c2", "Лымэли"), ("c3", "Вякгы")):
        _role(db, cid, name)
    out = handle_update(db, _audio(name="KP_Ch13_Lymeli_VetrovaOlga.wav"), now=NOW)
    row = db.query(BotUpload).one()
    assert (row.state, row.character_id, row.role_name) == ("queued", "c2", "Лымэли")
    assert _enqueued(out) == [{"id": row.id}]


def test_several_roles_ask_with_buttons(db):
    for cid, name in (("c1", "Сэкле"), ("c2", "Лымэли")):
        _role(db, cid, name)
    out = handle_update(db, _audio(name="zapis.wav"), now=NOW)
    row = db.query(BotUpload).one()
    assert row.state == "choose" and row.purpose == "take" and not _enqueued(out)
    assert out[0].payload["text"] == "Какая это роль?"
    assert sorted(_buttons(out[0].payload)) == sorted([
        ("Сэкле", f"bt:c:{row.id}:c1"), ("Лымэли", f"bt:c:{row.id}:c2"), ("Отмена", f"bt:x:{row.id}")])
    picked = handle_update(db, _press(f"bt:c:{row.id}:c2"), now=NOW)
    assert db.get(BotUpload, row.id).state == "queued" and db.get(BotUpload, row.id).role_name == "Лымэли"
    assert picked[1].payload["text"] == QUEUED and _enqueued(picked) == [{"id": row.id}]


def test_cancel_and_foreign_press(db):
    for cid, name in (("c1", "Сэкле"), ("c2", "Лымэли")):
        _role(db, cid, name)
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert [o.method for o in handle_update(db, _press(f"bt:c:{row.id}:c1", tid="42"), now=NOW)] == \
        ["answerCallbackQuery"]
    out = handle_update(db, _press(f"bt:x:{row.id}"), now=NOW)
    assert db.get(BotUpload, row.id).state == "cancelled" and "Отменено" in out[1].payload["text"]


def test_no_active_book_falls_through_to_auditions(db):
    _role(db, "c1", "Лымэли")
    _invite(db)
    set_active_book(db, None, updated_by="t")
    db.commit()
    out = handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert row.purpose == "audition" and "Проба на «Дракон»" in out[0].payload["text"]


def test_role_and_invitation_ask_take_or_audition(db):
    _role(db, "c1", "Лымэли")
    _invite(db)
    out = handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert row.state == "choose" and not _enqueued(out)
    assert sorted(_buttons(out[0].payload)) == sorted([
        ("Дубль: «Лымэли»", f"bt:c:{row.id}:c1"), ("Проба: «Дракон»", f"bu:c:{row.id}:c9"),
        ("Отмена", f"bt:x:{row.id}")])
    picked = handle_update(db, _press(f"bu:c:{row.id}:c9"), now=NOW)
    row = db.get(BotUpload, row.id)
    assert (row.state, row.purpose, row.character_id) == ("queued", "audition", "c9")
    assert _enqueued(picked, "enqueue_bot_upload") == [{"id": row.id}] and not _enqueued(picked)


def test_double_press_queues_once(db):
    for cid, name in (("c1", "Сэкле"), ("c2", "Лымэли")):
        _role(db, cid, name)
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    first = handle_update(db, _press(f"bt:c:{row.id}:c1"), now=NOW)
    second = handle_update(db, _press(f"bt:c:{row.id}:c2"), now=NOW)
    assert _enqueued(first) == [{"id": row.id}] and not _enqueued(second)
    assert db.get(BotUpload, row.id).character_id == "c1"


def test_a_stale_queued_row_does_not_block_a_resend(db):
    """Задача, застрявшая «в очереди» дольше STALE_AFTER (воркер упал), не держит файл навсегда."""
    _role(db, "c1", "Лымэли")
    handle_update(db, _audio(unique="SAME"), now=NOW)
    out = handle_update(db, _audio(unique="SAME"), now=NOW + timedelta(hours=7))
    assert _enqueued(out) and db.query(BotUpload).count() == 2


def test_same_file_twice_is_refused(db):
    _role(db, "c1", "Лымэли")
    handle_update(db, _audio(unique="SAME"), now=NOW)
    out = handle_update(db, _audio(unique="SAME"), now=NOW + timedelta(hours=1))
    assert out[0].payload["text"] == "Этот файл уже принят 06.10." and not _enqueued(out)
    assert db.query(BotUpload).count() == 1
    # тот же файл от другого человека — не повтор этого
    _role(db, "c5", "Сэкле", actor="Иванов Пётр")
    other = handle_update(db, _audio(tid="42", unique="SAME"), now=NOW)
    assert _enqueued(other)


def test_tentative_role_is_not_a_take(db):
    _role(db, "c1", "Лымэли", actor="Ветрова Ольга?")
    assert bot_takes.my_roles(db, tid="41", user=db.get(User, "u41"), book=db.get(ScriptBook, "b1")) == []
    out = handle_update(db, _audio(), now=NOW)
    assert "только по приглашению" in out[0].payload["text"] and db.query(BotUpload).count() == 0


def test_a_big_file_is_refused_with_its_limit(db, monkeypatch):
    _role(db, "c1", "Лымэли")
    monkeypatch.setattr(bot_takes, "max_download_bytes", lambda: 2 * 1024 * 1024)
    out = handle_update(db, _audio(), now=NOW)
    assert "больше 2 МБ" in out[0].payload["text"] and db.query(BotUpload).count() == 0


def test_migration_adds_columns_twice_safely():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0057_bot_takes.py"
    spec = importlib.util.spec_from_file_location("rev0057", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.down_revision == "0056_recording_book"
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE bot_uploads (id VARCHAR(12) PRIMARY KEY, state VARCHAR(10))"))
        conn.execute(sa.text("INSERT INTO bot_uploads (id, state) VALUES ('a', 'stored')"))
        for _ in range(2):
            with Operations.context(MigrationContext.configure(conn)):
                mod.upgrade()
        cols = {c["name"] for c in sa.inspect(conn).get_columns("bot_uploads")}
        assert {"purpose", "role_name", "book_id", "md5", "result_json", "stored_key"} <= cols
        assert conn.execute(sa.text("SELECT purpose FROM bot_uploads")).scalar() == "audition"


def test_webhook_queues_take_and_reports_failure(db, monkeypatch):
    """Очередь не взяла дубль — строка не висит «queued»: иначе повтор файла бот отклонит
    как «уже принят», а отчёт так и не придёт."""
    import contextlib

    from app.api import telegram_webhook
    from app.services.bot_out import Outgoing

    _role(db, "c1", "Лымэли")
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    db.commit()
    queued, api = [], []
    monkeypatch.setattr(telegram_webhook, "SessionLocal", lambda: contextlib.nullcontext(db))
    monkeypatch.setattr(telegram_webhook, "handle_update",
                        lambda session, update, now: [Outgoing("enqueue_bot_take", {"id": row.id})])
    monkeypatch.setattr(telegram_webhook, "enqueue_take", lambda upload_id: queued.append(upload_id) or None)
    monkeypatch.setattr(telegram_webhook, "call_bot_api", lambda method, payload: api.append(payload))
    telegram_webhook.process_update({"update_id": 1})
    assert queued == [row.id] and db.get(BotUpload, row.id).state == "failed"
    assert api == [{"chat_id": "41", "text": "Не получилось взять файл в работу — пришлите его ещё раз."}]


def _message(tid="41", **attachment):
    return {"update_id": 5, "message": {"message_id": 7, "from": {"id": int(tid)},
                                        "chat": {"id": int(tid), "type": "private"}, **attachment}}


@pytest.mark.parametrize("attachment", [
    {"sticker": {"file_id": "S", "file_unique_id": "S1"}},
    {"photo": [{"file_id": "P", "file_unique_id": "P1", "file_size": 10}]},
])
def test_photo_or_sticker_gets_no_take_reply(db, attachment):
    """Ревью C2: фото или стикер от человека не должны слышать «пришлите аудио»."""
    from app.services.bot_uploads import incoming_file

    _role(db, "c1", "Лымэли")
    info = incoming_file(_message(**attachment)["message"])
    assert bot_takes.receive(db, tid="41", chat_id=41, user=db.get(User, "u41"), info=info, now=NOW) is None
    out = handle_update(db, _message(**attachment), now=NOW)
    assert not any("аудио" in str(o.payload.get("text", "")) for o in out)
    assert db.query(BotUpload).count() == 0


def _no_book(db):
    set_active_book(db, None, updated_by="t")
    db.commit()


def test_no_active_book_tells_an_approved_dictor(db):
    _role(db, "c1", "Лымэли")
    _no_book(db)
    out = handle_update(db, _audio(), now=NOW)
    assert out[0].payload["text"] == "Сейчас студия не записывает ни одну книгу. Если это ошибка, напишите в студию."
    assert db.query(BotUpload).count() == 0


@pytest.mark.parametrize("filename", ["zapis.mp3", "zapis.ogg", "zapis.m4a", "zapis.aiff"])
def test_other_formats_do_not_enter_the_take_queue(db, filename):
    from app.services.take_format import REQUIRED

    _role(db, "c1", "Лымэли")
    out = handle_update(db, _audio(name=filename), now=NOW)
    assert out[0].payload["text"] == REQUIRED
    assert not _enqueued(out) and db.query(BotUpload).count() == 0


def test_mp3_audition_remains_allowed_with_an_approved_role(db):
    from app.services.take_format import REQUIRED

    _role(db, "c1", "Лымэли")
    _invite(db)
    handle_update(db, _audio(name="proba.mp3"), now=NOW)
    row = db.query(BotUpload).one()
    refused = handle_update(db, _press(f"bt:c:{row.id}:c1"), now=NOW)
    assert refused[-1].payload["text"] == REQUIRED and not _enqueued(refused)
    accepted = handle_update(db, _press(f"bu:c:{row.id}:c9"), now=NOW)
    assert row.purpose == "audition" and row.state == "queued"
    assert _enqueued(accepted, "enqueue_bot_upload") and not _enqueued(accepted)


def test_no_active_book_invited_only_gets_auditions(db):
    _invite(db)
    _no_book(db)
    out = handle_update(db, _audio(), now=NOW)
    assert "Проба на «Дракон»" in out[0].payload["text"]


def test_no_active_book_unknown_person_unchanged(db):
    _no_book(db)
    out = handle_update(db, _audio(tid="42"), now=NOW)
    assert "только по приглашению" in out[0].payload["text"] and db.query(BotUpload).count() == 0


@pytest.mark.parametrize("state, text", [("done", "Этот файл уже обработан."),
                                         ("failed", "Этот файл уже передан студии."),
                                         ("unsorted", "Этот файл уже передан студии.")])
def test_cancel_after_processing_names_the_state(db, state, text):
    _role(db, "c1", "Лымэли")
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    row.state = state
    db.commit()
    out = handle_update(db, _press(f"bt:x:{row.id}"), now=NOW)
    assert out[1].payload["text"] == text and db.get(BotUpload, row.id).state == state
