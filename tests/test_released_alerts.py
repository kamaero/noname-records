"""Владелец узнаёт о дубле, рекасте и пропусках в уже сданной главе (жизненный цикл книги)."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services import released_alerts
from app.services.audio_uploads import derive_book_code

NOW = datetime(2026, 10, 29, 12, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Карта", display_title="Карта", source_filename="x.docx",
                               source_format="docx", total_chars=0, author_sheets_x1000=0, chapter_count=3,
                               has_chapters="true", status="processing"))
        session.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=1, chapter_title="Глава 1", released_at=NOW))
        session.add(ScriptChapter(id="ch2", book_id="b1", chapter_index=2, chapter_title="Глава 2", released_at=NOW))
        session.add(ScriptChapter(id="ch3", book_id="b1", chapter_index=3, chapter_title="Глава 3"))
        session.commit()
        yield session


def _file(db, chapter, name, kind="take"):
    row = AudioFile(book_code=derive_book_code("Карта"), original_filename=name, stored_key=f"k/{name}",
                    mime_type="audio/wav", size_bytes=1, chapter=chapter, role="Ныкфо",
                    actor_name="Ветрова Ольга", kind=kind, canonical_filename=name)
    db.add(row)
    db.commit()
    return row


class Sink:
    def __init__(self):
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return True


@pytest.fixture(autouse=True)
def _base(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "app_base_url", "https://noname.example")


def test_link_uses_the_configured_base(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "app_base_url", "https://example.test/")
    item = _file(db, "Глава 1", "a.wav")
    sink = Sink()
    released_alerts.notify_takes_in_released(db, [item], send=sink)
    assert f"https://example.test/api/v2/takes/{item.id}/audio" in sink.texts[0]


def test_empty_base_sends_the_filename_without_a_link(db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "app_base_url", "")
    item = _file(db, "Глава 1", "a.wav")
    sink = Sink()
    assert released_alerts.notify_takes_in_released(db, [item], send=sink) is True
    assert "a.wav" in sink.texts[0] and "/api/v2/takes/" not in sink.texts[0]


def test_one_alert_for_a_batch_into_two_released_chapters(db):
    items = [_file(db, "Глава 1", "a.wav"), _file(db, "Глава 2", "b.wav"), _file(db, "Глава 3", "c.wav")]
    sink = Sink()
    assert released_alerts.notify_takes_in_released(db, items, send=sink) is True
    assert len(sink.texts) == 1
    text = sink.texts[0]
    assert "В сданную главу пришёл дубль" in text
    assert "a.wav" in text and "b.wav" in text and "c.wav" not in text
    assert text.count("https://noname.example/api/v2/takes/") == 2
    assert f"/api/v2/takes/{items[0].id}/audio" in text


def test_no_alert_for_takes_into_unreleased_chapters(db):
    items = [_file(db, "Глава 3", "c.wav"), _file(db, "Глава 1", "p.wav", kind="audition")]
    sink = Sink()
    assert released_alerts.notify_takes_in_released(db, items, send=sink) is False
    assert sink.texts == []


def test_recast_alert_only_when_the_role_speaks_in_a_released_chapter(db):
    db.add(Character(id="c1", book_id="b1", name="Ныкфо", aliases="", character_color="", appears_in="1,3"))
    db.add(Character(id="c2", book_id="b1", name="Хдыбдяр", aliases="", character_color="", appears_in="3"))
    db.commit()
    sink = Sink()
    assert released_alerts.notify_recast_in_released(
        db, db.get(Character, "c1"), old_actor="Ветрова Ольга", new_actor="Иванов Пётр", send=sink) is True
    assert len(sink.texts) == 1 and "Ныкфо" in sink.texts[0] and "Иванов Пётр" in sink.texts[0]
    assert released_alerts.notify_recast_in_released(
        db, db.get(Character, "c2"), old_actor="Ветрова Ольга", new_actor="Иванов Пётр", send=sink) is False
    assert len(sink.texts) == 1


def test_missing_alert_only_for_a_released_chapter(db):
    sink = Sink()
    missing = {"Ныкфо": ["Привет."]}
    assert released_alerts.notify_missing_in_released(db, "ch1", missing, send=sink) is True
    assert "Привет." in sink.texts[0]
    assert released_alerts.notify_missing_in_released(db, "ch3", missing, send=sink) is False
    assert released_alerts.notify_missing_in_released(db, "ch1", {}, send=sink) is False
    assert len(sink.texts) == 1


def test_send_failure_is_reported_as_not_sent(db):
    def boom(_text):
        raise RuntimeError("telegram down")

    items = [_file(db, "Глава 1", "a.wav")]
    assert released_alerts.notify_takes_in_released(db, items, send=boom) is False


def test_sync_book_reports_a_recast_and_survives_a_send_failure(db, monkeypatch):
    from app.services import role_deadlines

    db.add(Character(id="c1", book_id="b1", name="Ныкфо", aliases="", character_color="",
                     appears_in="1", actor_name="Ветрова Ольга"))
    db.commit()
    role_deadlines.sync_book(db, "b1", now=NOW)
    db.get(Character, "c1").actor_name = "Иванов Пётр"
    db.commit()
    calls = []
    monkeypatch.setattr(released_alerts, "notify_recast_in_released",
                        lambda _db, ch, *, old_actor, new_actor, send=None: calls.append((ch.id, old_actor, new_actor)))
    role_deadlines.sync_book(db, "b1", now=NOW)
    assert calls == [("c1", "Ветрова Ольга", "Иванов Пётр")]

    db.get(Character, "c1").actor_name = "Сидоров Иван"
    db.commit()

    def boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(released_alerts, "notify_recast_in_released", boom)
    assert role_deadlines.sync_book(db, "b1", now=NOW)["closed"] == 1


def test_book_code_is_matched_like_everywhere_else_not_by_exact_equality(db):
    """Файл, подписанный той же книгой в другом написании кода, всё равно попадает в сданную главу."""
    row = _file(db, "Глава 1", "a.wav")
    row.book_code = derive_book_code("Карта").lower()
    db.commit()
    sink = Sink()
    assert released_alerts.notify_takes_in_released(db, [row], send=sink) is True
