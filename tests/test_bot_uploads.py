"""Приём проб через бот (пакет C2, 06.10)."""
import contextlib
import io
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import BotUpload, Character, RoleDeadline, ScriptBook, TelegramAuthAccount, User, UserRole
from app.services import bot_status, bot_uploads, onboarding
from app.services.bot_dialog import handle_update

NOW = datetime(2026, 10, 6, 9, 0, 0)
CHATS = {"Ветрова Ольга": "41", "Иванов Пётр": "42"}


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
        for cid, role in (("c1", "Дракон"), ("c2", "Убув")):
            session.add(Character(id=cid, book_id="b1", name=role, actor_name="Ветрова Ольга?", appears_in="1"))
        session.add(RoleDeadline(id="d1", character_id="c1", book_id="b1", actor_name="Ветрова Ольга",
                                 kind="audition", due_at=NOW + timedelta(hours=20), created_at=NOW))
        session.commit()
        yield session


def _audio(tid="41", **extra):
    message = {"message_id": 7, "from": {"id": int(tid)}, "chat": {"id": int(tid), "type": "private"},
               "audio": {"file_id": "F" * 80, "file_unique_id": "U1", "mime_type": "audio/mpeg",
                         "file_name": "proba.mp3", "file_size": 3_000_000, "duration": 40}}
    message.update(extra)
    return {"update_id": 1, "message": message}


def _press(data, tid="41"):
    return {"update_id": 2, "callback_query": {"id": "cb", "from": {"id": int(tid)}, "data": data,
                                               "message": {"message_id": 9, "chat": {"id": int(tid), "type": "private"}}}}


def _data(payload):
    return [b["callback_data"] for row in payload["reply_markup"]["inline_keyboard"] for b in row]


def test_one_invitation_asks_to_confirm(db):
    out = handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert row.state == "choose" and row.kind == "audio" and row.file_id == "F" * 80
    assert "Audition for «Дракон» («Карта Приозерья»)?" in out[0].payload["text"]
    assert _data(out[0].payload) == [f"bu:c:{row.id}:c1", f"bu:x:{row.id}"]


def test_several_invitations_offer_roles(db):
    db.add(RoleDeadline(id="d2", character_id="c2", book_id="b1", actor_name="Ветрова Ольга", kind="audition",
                        due_at=NOW + timedelta(hours=20), created_at=NOW))
    db.commit()
    out = handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert sorted(_data(out[0].payload)) == sorted([f"bu:c:{row.id}:c1", f"bu:c:{row.id}:c2", f"bu:x:{row.id}"])


def test_no_invitation_no_audition(db):
    out = handle_update(db, _audio(tid="42"), now=NOW)
    assert "by invitation" in out[0].payload["text"] and db.query(BotUpload).count() == 0


def test_voice_is_accepted_and_video_is_not(db):
    voice = _audio()
    voice["message"].pop("audio")
    voice["message"]["voice"] = {"file_id": "V" * 60, "file_unique_id": "U2", "mime_type": "audio/ogg",
                                 "file_size": 50_000, "duration": 12}
    handle_update(db, voice, now=NOW)
    assert db.query(BotUpload).one().kind == "voice"
    video = _audio()
    video["message"].pop("audio")
    video["message"]["video"] = {"file_id": "X", "file_size": 10}
    out = handle_update(db, video, now=NOW)
    assert "audio file or voice message" in out[0].payload["text"]


def _sized(size):
    return _audio(audio={"file_id": "F", "file_unique_id": "U", "mime_type": "audio/wav",
                         "file_name": "big.wav", "file_size": size})


def test_a_big_file_goes_to_the_site(db, monkeypatch):
    monkeypatch.setattr(bot_uploads, "max_download_bytes", lambda: 20 * 1024 * 1024)
    out = handle_update(db, _sized(20 * 1024 * 1024 + 1), now=NOW)
    assert "larger than 20 MB" in out[0].payload["text"] and db.query(BotUpload).count() == 0


def test_a_file_exactly_at_the_limit_is_accepted(db, monkeypatch):
    monkeypatch.setattr(bot_uploads, "max_download_bytes", lambda: 20 * 1024 * 1024)
    handle_update(db, _sized(20 * 1024 * 1024), now=NOW)
    assert db.query(BotUpload).count() == 1


def test_local_mode_takes_a_35mb_file(db, monkeypatch):
    """Свой узел Bot API (21.10): студийный WAV больше 20 МБ принимается ботом."""
    monkeypatch.setattr(bot_uploads, "max_download_bytes", lambda: 2000 * 1024 * 1024)
    out = handle_update(db, _sized(35 * 1024 * 1024), now=NOW)
    assert db.query(BotUpload).count() == 1 and "больше" not in out[0].payload.get("text", "")


def test_the_local_limit_reads_as_gigabytes(db, monkeypatch):
    """«Файл больше 2000 МБ» диктор не поймёт с первого раза — пишем «2 ГБ»."""
    monkeypatch.setattr(bot_uploads, "max_download_bytes", lambda: 2000 * 1024 * 1024)
    out = handle_update(db, _sized(2001 * 1024 * 1024), now=NOW)
    assert "larger than 2 GB" in out[0].payload["text"]


def test_confirm_queues_once(db):
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    out = handle_update(db, _press(f"bu:c:{row.id}:c1"), now=NOW)
    assert any(o.method == "enqueue_bot_upload" and o.payload == {"id": row.id} for o in out)
    assert db.get(BotUpload, row.id).state == "queued" and db.get(BotUpload, row.id).character_id == "c1"
    again = handle_update(db, _press(f"bu:c:{row.id}:c1"), now=NOW)
    assert not any(o.method == "enqueue_bot_upload" for o in again)


def test_cancel_stale_foreign_and_closed(db):
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    assert [o.method for o in handle_update(db, _press(f"bu:c:{row.id}:c1", tid="42"), now=NOW)] == ["answerCallbackQuery"]
    late = handle_update(db, _press(f"bu:c:{row.id}:c1"), now=NOW + timedelta(hours=25))
    assert "expired" in late[1].payload["text"] and db.get(BotUpload, row.id).state == "choose"
    db.get(RoleDeadline, "d1").closed_at = NOW
    db.commit()
    closed = handle_update(db, _press(f"bu:c:{row.id}:c1"), now=NOW)
    assert "no longer current" in closed[1].payload["text"]
    out = handle_update(db, _press(f"bu:x:{row.id}"), now=NOW)
    assert db.get(BotUpload, row.id).state == "cancelled" and "Cancelled" in out[1].payload["text"]


def test_text_while_choosing_is_not_an_intro_name(db):
    handle_update(db, _audio(), now=NOW)
    out = handle_update(db, {"update_id": 3, "message": {"message_id": 8, "from": {"id": 41},
                                                         "chat": {"id": 41, "type": "private"}, "text": "привет"}},
                        now=NOW)
    assert "/status" in out[0].payload["text"]


from app.models import AudioFile, AuditLog  # noqa: E402


def _queued(db, kind="audio", mime="audio/mpeg", name="proba.mp3"):
    row = BotUpload(id="up1", telegram_user_id="41", file_id="FILE", file_unique_id="U", kind=kind, mime_type=mime,
                    file_name=name, file_size=10, duration=5, character_id="c1", actor_name="Ветрова Ольга",
                    state="queued", created_at=NOW, updated_at=NOW)
    db.add(row)
    db.commit()
    return row


def _fetch(content):
    """Подмена `telegram.fetched_file`: файл открыт на время сохранения."""
    @contextlib.contextmanager
    def fetch(file_id):
        assert file_id == "FILE"
        yield io.BytesIO(content)
    return fetch


def _no_fetch(file_id):
    pytest.fail("скачивать не нужно")


def _fake_store(db, **kw):
    item = AudioFile(book_code=kw["book_code"], original_filename=kw["original_filename"], stored_key="k/up1",
                     mime_type=kw["mime_type"], size_bytes=len(kw["source"].read()), chapter=kw["chapter"], role=kw["role"],
                     actor_name=kw["actor_name"], kind=kw["kind"], codec=kw.pop("_codec", "mp3"))
    db.add(item)
    db.flush()
    return item


def test_store_saves_under_the_cast_name_and_sends_the_card_by_file_id(db, monkeypatch):
    _queued(db)
    cards, texts = [], []
    monkeypatch.setattr(bot_uploads, "send_card", lambda db, audio_id, **kw: cards.append((audio_id, kw["file_id"])) or 2)
    result = bot_uploads.store(db, "up1", fetch=_fetch(b"ID3abc"),
                               send_text=lambda chat, text: texts.append((chat, text)) or True,
                               send_audio=lambda chat, **kw: True, store_file=_fake_store, now=NOW)
    audio = db.query(AudioFile).one()
    assert result == "stored" and db.get(BotUpload, "up1").state == "stored" and db.get(BotUpload, "up1").audio_id == audio.id
    assert (audio.kind, audio.role, audio.actor_name, audio.chapter) == ("audition", "Дракон", "Ветрова Ольга", "")
    assert texts == [("41", "Audition received — «Дракон». The response will arrive here.")]
    assert cards == [(audio.id, "FILE")]
    assert db.query(AuditLog).filter_by(action="bot_upload_stored").count() == 1


def test_a_wav_document_goes_to_the_card_from_storage(db, monkeypatch):
    """Документ WAV по `file_id` в плеер не отдать — карточка возьмёт файл из хранилища (MP3-копия)."""
    _queued(db, kind="document", mime="audio/wav", name="proba.wav")
    cards = []
    monkeypatch.setattr(bot_uploads, "send_card", lambda db, audio_id, **kw: cards.append(kw["file_id"]) or 2)
    store = lambda db, **kw: _fake_store(db, _codec="pcm_s24le", **kw)  # noqa: E731
    bot_uploads.store(db, "up1", fetch=_fetch(b"RIFF"), send_text=lambda c, t: True,
                      send_audio=lambda c, **kw: True, store_file=store, now=NOW)
    assert cards == [""]


def test_a_failed_download_marks_failed_and_tells_the_dictor(db, monkeypatch):
    _queued(db)
    monkeypatch.setattr(bot_uploads, "send_card", lambda *a, **k: pytest.fail("карточки быть не должно"))

    @contextlib.contextmanager
    def broken(file_id):
        raise OSError("network")
        yield

    texts = []
    result = bot_uploads.store(db, "up1", fetch=broken,
                               send_text=lambda chat, text: texts.append(text) or True,
                               send_audio=lambda c, **kw: True, store_file=_fake_store, now=NOW)
    assert result == "failed" and db.get(BotUpload, "up1").state == "failed" and db.query(AudioFile).count() == 0
    assert "Could not save the audition" in texts[0]


def test_store_skips_rows_not_queued(db):
    row = _queued(db)
    row.state = "stored"
    db.commit()
    assert bot_uploads.store(db, "up1", fetch=_no_fetch,
                             send_text=None, send_audio=None, store_file=_fake_store, now=NOW) == "skip"


def test_cancel_after_yes_does_not_pretend(db):
    """Ревью: «Отмена» после «Да» отвечала «Отменено», а проба всё равно уходила карточкой."""
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    handle_update(db, _press(f"bu:c:{row.id}:c1"), now=NOW)
    out = handle_update(db, _press(f"bu:x:{row.id}"), now=NOW)
    assert db.get(BotUpload, row.id).state == "queued"
    assert "already been accepted" in out[1].payload["text"] and "Cancelled" not in out[1].payload["text"]


def test_yes_fixes_the_sender_and_promises_a_reply(db):
    handle_update(db, _audio(), now=NOW)
    row = db.query(BotUpload).one()
    out = handle_update(db, _press(f"bu:c:{row.id}:c1"), now=NOW)
    assert db.get(BotUpload, row.id).actor_name == "Ветрова Ольга"
    assert "Confirmation will arrive" in out[1].payload["text"]


def test_a_recast_before_the_worker_does_not_credit_someone_else(db, monkeypatch):
    """Ревью: очередь общая с конвейером — задача могла ждать час, а за это время роль
    отдали другому; файл лёг бы на чужое имя. Имя фиксируется при «Да»."""
    _queued(db)
    db.get(BotUpload, "up1").actor_name = "Ветрова Ольга"
    db.get(Character, "c1").actor_name = "Иванов Пётр?"
    db.get(RoleDeadline, "d1").actor_name = "Иванов Пётр"
    db.commit()
    monkeypatch.setattr(bot_uploads, "send_card", lambda *a, **k: pytest.fail("карточки быть не должно"))
    texts = []
    result = bot_uploads.store(db, "up1", fetch=_no_fetch,
                               send_text=lambda chat, text: texts.append(text) or True,
                               send_audio=None, store_file=_fake_store, now=NOW)
    assert result == "failed" and db.query(AudioFile).count() == 0
    assert "role has been reassigned" in texts[0]


def test_an_ogg_sent_as_music_goes_from_storage_not_by_voice_file_id(db, monkeypatch):
    _queued(db, kind="audio", mime="audio/ogg", name="proba.ogg")
    db.get(BotUpload, "up1").actor_name = "Ветрова Ольга"
    db.commit()
    cards = []
    monkeypatch.setattr(bot_uploads, "send_card", lambda db, audio_id, **kw: cards.append(kw["file_id"]) or 2)
    store = lambda db, **kw: _fake_store(db, _codec="opus", **kw)  # noqa: E731
    bot_uploads.store(db, "up1", fetch=_fetch(b"OggS"), send_text=lambda c, t: True,
                      send_audio=lambda c, **kw: True, store_file=store, now=NOW)
    assert cards == [""]


def test_a_photo_from_someone_without_an_invitation_gets_the_usual_reply(db):
    photo = _audio(tid="42")
    photo["message"].pop("audio")
    photo["message"]["photo"] = [{"file_id": "P"}]
    out = handle_update(db, photo, now=NOW)
    assert "аудиофайлом" not in out[0].payload["text"] and "/status" in out[0].payload["text"]
