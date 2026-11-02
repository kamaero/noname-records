"""Воркер сохраняет пересланные демо и ссылки в карточку диктора (проект Б)."""
import contextlib
import io
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import BotDemoIntake, BotDemoItem, DictorDemo, DictorLink, User, UserRole
from app.services import bot_demos
from app.services.demo_fetch import FetchError

NOW = datetime(2026, 10, 23, 9, 0, 0)
OWNER = "900000100"


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(settings, "owner_telegram_id", OWNER)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(User(id="u41", login="tg_41", password_hash="x", display_name="Ветрова Ольга", is_active="true"))
        session.add(UserRole(user_id="u41", role="dictor"))
        session.add(BotDemoIntake(id="in1", sender_key="tg:41", telegram_user_id="41", state="resolved",
                                  user_id="u41", created_at=NOW, updated_at=NOW))
        session.commit()
        yield session


@pytest.fixture()
def mp3(monkeypatch):
    """ffmpeg не зовём: «конвертация» кладёт байты, md5 — от содержимого исходника."""
    import hashlib

    def fake(src, target):
        data = open(src, "rb").read()
        with open(target, "wb") as handle:
            handle.write(b"mp3" + data)
        return {"duration_seconds": 30.0, "size_bytes": len(data) + 3, "md5": hashlib.md5(data).hexdigest(),
                "trimmed": False}
    monkeypatch.setattr("app.services.demo_audio.to_demo_mp3", fake)


def _item(db, kind="file", ref="FILE1", **extra):
    fields = {"file_name": "Демо Ветрова сказка.mp3", "mime_type": "audio/mpeg", "file_size": 3_000}
    fields.update(extra)
    item = BotDemoItem(id=f"it{db.query(BotDemoItem).count()}", intake_id="in1", source_message_id=100 + db.query(
        BotDemoItem).count(), kind=kind, ref=ref, state="queued", created_at=NOW, updated_at=NOW, **fields)
    db.add(item)
    db.commit()
    return item


def _fetch(data=b"audio-bytes"):
    calls = []

    @contextlib.contextmanager
    def fetch(file_id):
        calls.append(file_id)
        yield io.BytesIO(data)
    fetch.calls = calls
    return fetch


def _no_download(url, workdir):
    raise AssertionError("скачивание не ожидалось")


def _sent():
    out = []
    return out, (lambda chat, text: out.append((chat, text)) or True)


def test_file_stored_with_telegram_source(db, mp3):
    item = _item(db)
    sent, send_text = _sent()
    assert bot_demos.store_item(db, item.id, fetch=_fetch(), download=_no_download, send_text=send_text,
                                now=NOW) == "stored"
    demo = db.query(DictorDemo).one()
    assert (demo.user_id, demo.source, demo.title) == ("u41", "telegram", "сказка")
    assert db.get(BotDemoItem, item.id).state == "stored"
    assert sent[0][0] == OWNER and "Ветрова Ольга" in sent[0][1] and "/app/dictors" in sent[0][1]


def test_voice_title(db, mp3):
    item = _item(db, file_name="voice.ogg", mime_type="audio/ogg")
    _, send_text = _sent()
    bot_demos.store_item(db, item.id, fetch=_fetch(), download=_no_download, send_text=send_text, now=NOW)
    assert db.query(DictorDemo).one().title == "голосовое"


def test_yandex_folder_two_files_two_demos(db, mp3, tmp_path):
    item = _item(db, kind="link", ref="https://disk.yandex.ru/d/folder", file_name="", mime_type="", file_size=0)

    def download(url, workdir):
        paths = []
        for name, data in (("сказка.mp3", b"one"), ("реклама.wav", b"two")):
            path = f"{workdir}/{name}"
            open(path, "wb").write(data)
            paths.append(path)
        return paths
    sent, send_text = _sent()
    assert bot_demos.store_item(db, item.id, fetch=_fetch(), download=download, send_text=send_text,
                                now=NOW) == "stored"
    assert sorted(d.title for d in db.query(DictorDemo)) == ["реклама", "сказка"]


def test_download_failure_saves_link(db, mp3):
    url = "https://disk.yandex.ru/d/broken"
    item = _item(db, kind="link", ref=url, file_name="", mime_type="", file_size=0)

    def download(u, workdir):
        raise FetchError("yandex_failed")
    sent, send_text = _sent()
    assert bot_demos.store_item(db, item.id, fetch=_fetch(), download=download, send_text=send_text,
                                now=NOW) == "link_saved"
    assert db.query(DictorLink).filter_by(user_id="u41", url=url).count() == 1
    assert "Не скачалось" in sent[0][1] and url in sent[0][1]


def test_duplicate_reported(db, mp3):
    first, second = _item(db, ref="A"), _item(db, ref="B")
    sent, send_text = _sent()
    bot_demos.store_item(db, first.id, fetch=_fetch(b"same"), download=_no_download, send_text=send_text, now=NOW)
    assert bot_demos.store_item(db, second.id, fetch=_fetch(b"same"), download=_no_download, send_text=send_text,
                                now=NOW) == "duplicate"
    assert "уже есть" in sent[-1][1] and db.query(DictorDemo).count() == 1


def test_too_big_file_is_reported(db, mp3, monkeypatch):
    monkeypatch.setattr(bot_demos, "max_download_bytes", lambda: 20 * 1024 * 1024)
    item = _item(db, file_size=30 * 1024 * 1024)
    fetch = _fetch()
    sent, send_text = _sent()
    assert bot_demos.store_item(db, item.id, fetch=fetch, download=_no_download, send_text=send_text,
                                now=NOW) == "failed"
    assert fetch.calls == [] and "больше 20 МБ" in sent[0][1]


def test_unexpected_error_marks_failed(db, monkeypatch):
    def broken(src, target):
        raise RuntimeError("ffmpeg упал")
    monkeypatch.setattr("app.services.demo_audio.to_demo_mp3", broken)
    item = _item(db)
    sent, send_text = _sent()
    assert bot_demos.store_item(db, item.id, fetch=_fetch(), download=_no_download, send_text=send_text,
                                now=NOW) == "failed"
    assert db.get(BotDemoItem, item.id).state == "failed" and "пришлите ещё раз" in sent[0][1]


def test_not_queued_is_skipped(db):
    item = _item(db)
    item.state = "stored"
    db.commit()
    assert bot_demos.store_item(db, item.id, fetch=_fetch(), download=_no_download, send_text=None,
                                now=NOW) == "skip"


def test_webhook_queues_demo_and_reports_failure(db, monkeypatch):
    from app.api import telegram_webhook
    from app.services.bot_out import Outgoing

    item = _item(db)
    item.state = "queued"
    db.commit()
    queued, api = [], []
    monkeypatch.setattr(telegram_webhook, "SessionLocal", lambda: contextlib.nullcontext(db))
    monkeypatch.setattr(telegram_webhook, "handle_update",
                        lambda session, update, now: [Outgoing("enqueue_bot_demo", {"id": item.id})])
    monkeypatch.setattr(telegram_webhook, "enqueue_store_demo", lambda item_id: queued.append(item_id) or None)
    monkeypatch.setattr(telegram_webhook, "call_bot_api", lambda method, payload: api.append(payload))
    telegram_webhook.process_update({"update_id": 1})
    assert queued == [item.id]
    assert db.get(BotDemoItem, item.id).state == "failed"
    assert api and api[0]["chat_id"] == OWNER


def test_unconvertible_download_saves_link(db, monkeypatch):
    """Ревью I-3: Google Drive отдал HTML как «демо.bin», ffmpeg упал — ссылка терялась насовсем."""
    from app.services.demo_audio import ConvertError

    def broken(src, target):
        raise ConvertError("no_audio")
    monkeypatch.setattr("app.services.demo_audio.to_demo_mp3", broken)
    url = "https://drive.google.com/file/d/X/view"
    item = _item(db, kind="link", ref=url, file_name="", mime_type="", file_size=0)

    def download(u, workdir):
        path = f"{workdir}/демо.bin"
        open(path, "wb").write(b"<html>")
        return [path]
    sent, send_text = _sent()
    assert bot_demos.store_item(db, item.id, fetch=_fetch(), download=download, send_text=send_text,
                                now=NOW) == "link_saved"
    assert db.query(DictorLink).filter_by(user_id="u41", url=url).count() == 1


def test_one_bad_file_in_folder_keeps_the_rest(db, mp3, monkeypatch):
    from app.services import demo_audio

    real = demo_audio.to_demo_mp3

    def picky(src, target):
        if src.endswith("bad.mp3"):
            raise demo_audio.ConvertError("no_audio")
        return real(src, target)
    monkeypatch.setattr("app.services.demo_audio.to_demo_mp3", picky)
    item = _item(db, kind="link", ref="https://disk.yandex.ru/d/folder", file_name="", mime_type="", file_size=0)

    def download(u, workdir):
        paths = []
        for name, data in (("good.mp3", b"one"), ("bad.mp3", b"two")):
            open(f"{workdir}/{name}", "wb").write(data)
            paths.append(f"{workdir}/{name}")
        return paths
    _, send_text = _sent()
    assert bot_demos.store_item(db, item.id, fetch=_fetch(), download=download, send_text=send_text,
                                now=NOW) == "stored"
    assert [d.title for d in db.query(DictorDemo)] == ["good"]
