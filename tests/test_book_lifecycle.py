"""Жизненный цикл книги: «Глава сдана» и «Озвучена» (план 2026-10-29-book-lifecycle, задача 1)."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, RoleDeadline, ScriptBook, ScriptChapter
from app.services import book_lifecycle
from app.services.studio_settings import studio_settings
from app.v2 import lifecycle_api
from app.v2.reader import build_book_chapters

NOW = datetime(2026, 10, 29, 12, 0, 0)


@pytest.fixture()
def factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False)
    with maker() as db:
        db.add(ScriptBook(id="b1", title="Карта", display_title="Карта", source_filename="x.docx",
                          source_format="docx", total_chars=0, author_sheets_x1000=0, chapter_count=3,
                          has_chapters="true", status="processing"))
        for i, status in enumerate(["published", "published", "queued"], 1):
            db.add(ScriptChapter(id=f"ch{i}", book_id="b1", chapter_index=i, chapter_title=f"Глава {i}",
                                 status=status))
        db.commit()
    return maker


@pytest.fixture()
def db(factory):
    with factory() as session:
        yield session


def test_marking_a_chapter_released_and_back(db):
    assert book_lifecycle.mark_released(db, "ch1", released=True, now=NOW).released_at == NOW
    assert book_lifecycle.mark_released(db, "ch1", released=False).released_at is None
    with pytest.raises(LookupError):
        book_lifecycle.mark_released(db, "nope", released=True)


def test_all_published_released_ignores_unpublished_chapters(db):
    book_lifecycle.mark_released(db, "ch1", released=True)
    book_lifecycle.mark_released(db, "ch2", released=True)
    assert book_lifecycle.all_published_released(db, "b1") is True


def test_one_unreleased_published_chapter_blocks_voicing_offer(db):
    book_lifecycle.mark_released(db, "ch1", released=True)
    assert book_lifecycle.all_published_released(db, "b1") is False


def test_voice_book_clears_recording_book_and_closes_deadlines(db):
    studio_settings(db).recording_book_id = "b1"
    db.add(Character(id="c1", book_id="b1", name="Ныкфо", aliases="", character_color="", appears_in="1"))
    db.add(RoleDeadline(character_id="c1", book_id="b1", actor_name="Ветрова Ольга", kind="role", due_at=NOW))
    db.commit()
    result = book_lifecycle.voice_book(db, "b1", now=NOW)
    db.commit()
    assert result == {"voiced_at": NOW, "closed_deadlines": 1, "cleared_recording_book": True}
    assert studio_settings(db).recording_book_id == ""
    row = db.query(RoleDeadline).one()
    assert (row.closed_at, row.close_reason) == (NOW, "voiced")
    assert book_lifecycle.is_voiced(db.get(ScriptBook, "b1"))
    assert book_lifecycle.voiced_book_ids(db) == {"b1"}


def test_reopen_does_not_restore_recording_book_or_deadlines(db):
    studio_settings(db).recording_book_id = "b1"
    db.add(RoleDeadline(character_id="c1", book_id="b1", actor_name="Ветрова Ольга", kind="role", due_at=NOW))
    db.commit()
    book_lifecycle.voice_book(db, "b1", now=NOW)
    book_lifecycle.reopen_book(db, "b1")
    db.commit()
    assert not book_lifecycle.is_voiced(db.get(ScriptBook, "b1"))
    assert studio_settings(db).recording_book_id == ""
    assert db.query(RoleDeadline).one().close_reason == "voiced"


def _as(monkeypatch, factory, roles):
    monkeypatch.setattr(lifecycle_api, "SessionLocal", factory)
    monkeypatch.setattr(lifecycle_api, "is_authenticated", lambda _r: True)
    monkeypatch.setattr(lifecycle_api, "has_any_role", lambda _r, allowed: "admin" in roles or bool(set(roles) & allowed))


def test_released_endpoint_requires_admin(monkeypatch, factory):
    _as(monkeypatch, factory, ["author"])
    assert lifecycle_api.api_v2_chapter_released("ch1", SimpleNamespace(), {"released": True}).status_code == 403
    assert lifecycle_api.api_v2_book_voiced("b1", SimpleNamespace(), {"voiced": True}).status_code == 403
    with factory() as db:
        assert db.get(ScriptChapter, "ch1").released_at is None


def test_endpoints_work_for_admin(monkeypatch, factory):
    _as(monkeypatch, factory, ["admin"])
    out = lifecycle_api.api_v2_chapter_released("ch1", SimpleNamespace(), {"released": True})
    assert out["ok"] and out["released_at"] and out["all_released"] is False and out["voiced"] is False
    out = lifecycle_api.api_v2_chapter_released("ch2", SimpleNamespace(), {"released": True})
    assert out["all_released"] is True
    assert lifecycle_api.api_v2_chapter_released("zz", SimpleNamespace(), {"released": True}).status_code == 404
    voiced = lifecycle_api.api_v2_book_voiced("b1", SimpleNamespace(), {"voiced": True})
    assert voiced["ok"] and voiced["voiced_at"]
    assert lifecycle_api.api_v2_book_voiced("b1", SimpleNamespace(), {"voiced": False})["voiced_at"] == ""


def test_book_chapters_payload_carries_released_at(db):
    book_lifecycle.mark_released(db, "ch1", released=True, now=NOW)
    db.commit()
    chapters = build_book_chapters(db, "b1")["chapters"]
    assert chapters[0]["released_at"].startswith("2026-10-29")
    assert chapters[1]["released_at"] == ""


def test_workspace_chapters_carry_released_at(db):
    """Диктору в «Загрузке» и «Сценарии» сданная глава нужна пометкой, а не пропажей."""
    from app.services.recording_workspace import build_recording_workspace_payload

    book_lifecycle.mark_released(db, "ch1", released=True, now=NOW)
    db.commit()
    payload = build_recording_workspace_payload(db, book_id="b1")
    by_id = {c["id"]: c for c in payload["chapters"]}
    assert by_id["ch1"]["released_at"].startswith("2026-10-29")
    assert by_id["ch2"]["released_at"] == ""
