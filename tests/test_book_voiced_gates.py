"""Озвученная книга закрыта для дикторов, бота и сроков (жизненный цикл книги, задача 2).

Закрыто для всех, кроме владельца (`admin`): агент и автор — тоже нет, потому что запись
в законченную книгу не должна появляться «по доброте» чужой ручки.
"""
import asyncio
import io
import json
from datetime import datetime, timedelta

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.audio_uploads as au
from app.api.recording import build_recording_handlers
from app.db import Base
from app.models import AudioFile, Character, RoleDeadline, ScriptBook, ScriptChapter
from app.services import bot_reminder, bot_status, bot_studio, bot_uploads, role_deadlines
from app.services.audio_uploads import derive_book_code
from app.services.recording_workspace import build_recording_workspace_payload
from app.services.studio_settings import active_book, set_active_book
from app.time_utils import utcnow_naive

NOW = datetime(2026, 10, 29, 12, 0, 0)
TITLE = "Карта приозерья"
CODE = derive_book_code(TITLE)


@pytest.fixture()
def sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    maker = sessionmaker(bind=engine, autoflush=False)
    with maker() as db:
        for book_id, title, voiced in (("open", "Открытая книга", None), ("voiced", TITLE, NOW)):
            db.add(ScriptBook(id=book_id, title=title, display_title=title, source_filename="x", source_format="txt",
                              created_at=utcnow_naive(), voiced_at=voiced))
            db.add(ScriptChapter(id=f"{book_id}-ch1", book_id=book_id, chapter_index=1, chapter_title="Глава 1",
                                 status="published"))
            db.add(Character(id=f"{book_id}-c1", book_id=book_id, name="Ныкфо", actor_name="Ветрова Ольга",
                             appears_in="1"))
        db.commit()
    return maker


# --- рабочее место записи -------------------------------------------------

def _workspace(sessions, *, admin: bool):
    handlers = build_recording_handlers(_deps(sessions, roles={"admin"} if admin else {"dictor"}))
    request = _Request()
    request.query_params = {}
    response = asyncio.run(handlers["recording_workspace"](request))
    return json.loads(response.body)


def _chapter_book_ids(payload):
    return {chapter["id"] for chapter in payload["chapters"]}


def test_workspace_hides_a_voiced_book_from_a_dictor_and_an_agent(sessions):
    # агент не admin: has_any_role({"admin"}) для него ложь — так же, как для диктора
    assert _chapter_book_ids(_workspace(sessions, admin=False)) == {"open-ch1"}


def test_workspace_shows_a_voiced_book_to_admin(sessions):
    assert _chapter_book_ids(_workspace(sessions, admin=True)) == {"open-ch1", "voiced-ch1"}


# --- загрузка -------------------------------------------------------------

def _post(sessions, *, roles, book_code=CODE):
    handlers = build_recording_handlers(_deps(sessions, roles=roles))
    original, au.audio_storage = au.audio_storage, _FakeStorage()
    try:
        files = [UploadFile(filename="Ныкфо.wav", file=io.BytesIO(b"RIFF0000"))]
        meta = [{"book_code": book_code, "chapter": "Глава 1", "role": "Ныкфо", "actor_name": "Ветрова Ольга"}]
        response = asyncio.run(handlers["recording_batch"](_Request(), files=files, meta_json=json.dumps(meta)))
    finally:
        au.audio_storage = original
    return response.status_code, json.loads(response.body)


def test_batch_upload_into_a_voiced_book_is_refused_for_an_agent(sessions):
    status, body = _post(sessions, roles={"agent"})
    assert status == 409
    assert body == {"ok": False, "error": "book_voiced", "message": "The book is complete; uploads are closed."}
    with sessions() as db:
        assert db.query(AudioFile).count() == 0


def test_batch_upload_into_a_voiced_book_is_refused_for_a_dictor_and_an_author(sessions):
    for role in ("dictor", "author"):
        assert _post(sessions, roles={role})[0] == 409


def test_batch_upload_into_a_voiced_book_is_allowed_for_admin(sessions):
    status, body = _post(sessions, roles={"admin"})
    assert status == 200 and body["ok"] is True


def test_batch_upload_into_an_open_book_is_not_affected(sessions):
    status, body = _post(sessions, roles={"dictor"}, book_code=derive_book_code("Открытая книга"))
    assert status == 200 and body["ok"] is True


# --- бот ------------------------------------------------------------------

def test_bot_ignores_a_take_for_a_voiced_book(sessions):
    with sessions() as db:
        set_active_book(db, "voiced", updated_by="t")  # как если бы указатель остался на озвученной
        db.commit()
        assert active_book(db) is None
        set_active_book(db, "open", updated_by="t")
        db.commit()
        assert active_book(db).id == "open"


def test_bot_invitations_skip_a_voiced_book(sessions, monkeypatch):
    monkeypatch.setattr("app.services.bot_status.owner_by_chat", lambda db, tid, user: lambda name: True)
    with sessions() as db:
        for book_id in ("open", "voiced"):
            db.add(RoleDeadline(character_id=f"{book_id}-c1", book_id=book_id, actor_name="Ветрова Ольга",
                                kind="audition", due_at=NOW + timedelta(days=1), created_at=NOW))
        db.commit()
        assert [book.id for _, book in bot_uploads._invitations(db, "41", None)] == ["open"]


def test_bot_status_and_studio_omit_a_voiced_books_roles(sessions):
    with sessions() as db:
        db.add(RoleDeadline(character_id="voiced-c1", book_id="voiced", actor_name="Ветрова Ольга", kind="role",
                            due_at=NOW - timedelta(days=1), created_at=NOW))
        db.commit()
        lines = bot_status.person_lines(db, lambda name: True, now=NOW)
        assert TITLE not in " ".join(lines.roles) and "Открытая книга" in " ".join(lines.roles)
        studio = bot_studio.Studio(db, now=NOW)
        assert "voiced" not in studio.book_by_id and studio.open_deadlines == []
        assert all(book.title != TITLE for book in studio.books())


def test_reminder_targets_skip_a_voiced_book(sessions):
    with sessions() as db:
        db.add(RoleDeadline(character_id="voiced-c1", book_id="voiced", actor_name="Ветрова Ольга", kind="role",
                            due_at=NOW + timedelta(days=3), created_at=NOW))
        db.commit()
        assert bot_reminder.targets(db, book_id="") == []


# --- сроки ----------------------------------------------------------------

def test_deadline_pass_sends_nothing_for_a_voiced_book(sessions):
    with sessions() as db:
        row = RoleDeadline(character_id="voiced-c1", book_id="voiced", actor_name="Ветрова Ольга", kind="role",
                           due_at=NOW - timedelta(days=2), created_at=NOW - timedelta(days=10))
        db.add(row)
        db.commit()
        sent = []
        role_deadlines.run_pass(db, now=NOW, send=lambda chat, text: sent.append((chat, text)) or True)
        db.commit()
        assert sent == []
        db.refresh(row)
        assert row.closed_at is not None and row.close_reason == "voiced"


def test_sync_book_opens_no_deadline_in_a_voiced_book(sessions):
    with sessions() as db:
        assert role_deadlines.sync_book(db, "voiced", now=NOW) == {"opened": 0, "closed": 0}
        assert db.query(RoleDeadline).count() == 0
        assert role_deadlines.sync_book(db, "open", now=NOW)["opened"] == 1


# --- обвязка --------------------------------------------------------------

class _Request:
    headers: dict = {}


class _FakeStorage:
    def write_file(self, key, payload, location="nas"):
        import hashlib
        return hashlib.md5(payload).hexdigest()

    def write_stream(self, key, source, chunk_size=1024 * 1024, location="nas"):
        import hashlib
        digest = hashlib.md5()
        written = 0
        while chunk := source.read(65536):
            digest.update(chunk)
            written += len(chunk)
        return written, digest.hexdigest()

    def resolve_path(self, key, location="nas"):
        return f"/nowhere/{key}"

    def local_root(self):
        return "/nowhere"

    def free_bytes(self, path):
        return 10**12


def _deps(sessions, *, roles):
    return {
        "is_authenticated": lambda request: True,
        "has_any_role": lambda request, wanted: bool(set(wanted) & set(roles)),
        "has_workspace_full_access": lambda request: True,
        "session_payload": lambda request: {"display_name": "Ветрова Ольга", "sub": "tg_1"},
        "is_agent": lambda request: "agent" in roles,
        "SessionLocal": sessions,
        "AudioFile": AudioFile,
        "build_canonical_audio_filename": au.build_canonical_audio_filename,
        "store_audio_file": lambda db, **kwargs: au.store_audio_file(db, safe_name=lambda s: s, **kwargs),
        "send_telegram_message": lambda db, text: None,
        "recording_identity": lambda request, name: ("u1", name),
        "mark_role_progress_recorded": lambda *args: 0,
        "recording_workspace_payload": build_recording_workspace_payload,
        "require_recording_access": lambda request: None,
        "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": {
            "filename_chapter_index": 0,
        },
    }
