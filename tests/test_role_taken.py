"""Проба на роль, уже утверждённую за другим, не принимается (решение владельца 05.10).

Елмар Ервис прислал пробу на Хдыбдяра, накрепко утверждённого за Приваловым
Сергеем: форма предлагала весь каст, а сервер делил загрузку только на «твоя роль —
дубль, любая другая — проба». Роль со «?» (предложение агента) и свободная — как раньше.
"""
import asyncio
import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, Character, ScriptBook
from app.services.upload_kind import role_taken_by_other
from tests.test_chapter_mismatch_guard import (
    _JsonRequest, _post, _sessions, _upload,
)

NOW = datetime(2026, 10, 5, 9, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(ScriptBook(id="b1", title="Карта Приозерья", source_filename="k", source_format="txt", created_at=NOW))
        session.add(ScriptBook(id="b2", title="Сезон ветров", source_filename="s", source_format="txt", created_at=NOW))
        session.add(Character(book_id="b1", name="Хдыбдяр", aliases="Дзим", actor_name="Шелвар Шарита"))
        session.add(Character(book_id="b1", name="Саяху", actor_name="Кузнецова Светлана?"))
        session.add(Character(book_id="b1", name="Тик-Так", actor_name=""))
        session.commit()
        yield session


def _taken(db, role, actor="Елмар Ервис", code="КП"):
    return role_taken_by_other(db, book_code=code, role=role, actor_name=actor)


def test_a_role_approved_for_someone_else_is_taken(db):
    assert _taken(db, "Хдыбдяр") is True
    assert _taken(db, "хдыбдяр") is True          # регистр не в счёт
    assert _taken(db, "Дзим") is True             # псевдоним — та же роль


def test_a_tentative_or_free_role_is_open(db):
    assert _taken(db, "Саяху") is False           # «Имя?» — предложение, не утверждение
    assert _taken(db, "Тик-Так") is False
    assert _taken(db, "Кто-то новый") is False


def test_the_approved_actor_himself_is_not_blocked(db):
    assert _taken(db, "Хдыбдяр", actor="Шарита Шелвар") is False


def test_another_book_is_another_cast(db):
    assert _taken(db, "Хдыбдяр", code="СВ") is False


# ---------- ручки ----------

import tests.test_chapter_mismatch_guard as guard  # noqa: E402
import app.services.audio_uploads as au  # noqa: E402
from app.api.recording import build_recording_handlers  # noqa: E402
from app.services.shared_runtime import normalize_role_label  # noqa: E402


def _taken_sessions():
    """Нрарсх утверждён за Ермаковым; пробу шлёт Пухютпохов."""
    sessions = _sessions()
    with sessions() as db:
        book = db.query(ScriptBook).one()
        db.add(Character(book_id=book.id, name="Нрарсх", actor_name="Нартия Нилдар"))
        db.commit()
    return sessions


def _as(monkeypatch, *, owner: bool):
    """Оснастка `test_chapter_mismatch_guard`, но с правами диктора (owner=False) или
    владельца: правило о занятой роли владельца и автора не касается."""
    def handlers(sessions, sent=None):
        return build_recording_handlers({
            "is_authenticated": lambda request: True,
            "has_any_role": lambda request, roles: owner,
            "has_workspace_full_access": lambda request: True,
            "session_payload": lambda request: {"display_name": "Елмар Ервис", "sub": "tg_2"},
            "is_agent": lambda request: False,
            "SessionLocal": sessions,
            "AudioFile": AudioFile,
            "build_canonical_audio_filename": au.build_canonical_audio_filename,
            "store_audio_file": lambda db, **kwargs: au.store_audio_file(db, safe_name=lambda s: s, **kwargs),
            "send_telegram_message": lambda db, text: None,
            "recording_identity": lambda request, name: ("u2", name),
            "mark_role_progress_recorded": lambda *args: 0,
            "recording_workspace_payload": lambda db, **kwargs: {},
            "require_recording_access": lambda request: None,
            "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": au.parse_batch_audio_filename(
                filename, default_book_code=default_book_code, default_actor_name=default_actor_name,
                normalize_role_label=normalize_role_label,
            ),
        })
    monkeypatch.setattr(guard, "_recording_handlers", handlers)


def test_the_upload_refuses_an_audition_on_a_taken_role(monkeypatch):
    _as(monkeypatch, owner=False)
    sessions = _taken_sessions()
    meta = {"book_code": "KP", "chapter": "", "role": "Нрарсх", "actor_name": "Елмар Ервис"}

    status_code, body, rows = _post(sessions, [_upload("KP_Nrarsh_LarvikLavant_proba.wav")], [meta])

    assert status_code == 400 and rows == []
    assert body["items"][0]["error"] == "role_taken"
    assert "уже утверждена" in body["message"]


def test_the_preview_shows_the_same_refusal(monkeypatch):
    sessions = _taken_sessions()
    from app.api.dictor_uploads import build_dictor_upload_handlers
    handler = build_dictor_upload_handlers({
        "is_authenticated": lambda request: True,
        "has_any_role": lambda request, roles: False,
        "has_workspace_full_access": lambda request: True,
        "SessionLocal": sessions,
        "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": au.parse_batch_audio_filename(
            filename, default_book_code=default_book_code, default_actor_name=default_actor_name,
            normalize_role_label=normalize_role_label,
        ),
        "apply_batch_overrides": lambda parsed, override=None: au.apply_batch_overrides(
            parsed, override, normalize_role_label=normalize_role_label,
        ),
    })["dictor_pro_batch_validate"]
    payload = {
        "files": [{"name": "KP_Nrarsh_LarvikLavant_proba.wav", "size": 4}],
        "overrides": [{"chapter": "", "role": "Нрарсх", "actor_name": "Елмар Ервис", "book_code": "KP"}],
        "book_code": "KP", "actor_name": "Елмар Ервис",
    }
    item = json.loads(asyncio.run(handler(_JsonRequest(payload))).body)["items"][0]
    assert item["ok"] is False and "role_taken" in item["errors"]
    assert item["error_text"] == "роль «Нрарсх» уже утверждена — пробы на неё не принимаются"


def test_the_approved_actor_still_uploads_his_take():
    sessions = _taken_sessions()
    meta = {"book_code": "KP", "chapter": "Глава 1", "role": "Нрарсх", "actor_name": "Нартия Нилдар"}
    status_code, _body, rows = _post(sessions, [_upload("KP_Ch01_Nrarsh_NartiyaNildar.wav")], [meta])
    assert status_code == 200 and len(rows) == 1 and isinstance(rows[0], AudioFile)


def test_the_owner_may_upload_an_audition_onto_a_taken_role(monkeypatch):
    """Владелец решает каст сам — например, загружает пробу, присланную мимо системы, под рекаст."""
    _as(monkeypatch, owner=True)
    meta = {"book_code": "KP", "chapter": "", "role": "Нрарсх", "actor_name": "Елмар Ервис"}
    status_code, _body, rows = _post(_taken_sessions(), [_upload("KP_Nrarsh_LarvikLavant_proba.wav")], [meta])
    assert status_code == 200 and len(rows) == 1
