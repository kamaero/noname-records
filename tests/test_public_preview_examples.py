"""Пустой предпросмотр не зависит от студийного каста и сохраняет контракт ответа."""
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import Base
from app.models import AudioFile, ScriptBook
from app.services import book_thanks, role_deadlines
from app.services.audio_uploads import derive_book_code
from app.v2 import lifecycle_api


@pytest.mark.parametrize("has_recipient", [False, True])
def test_thanks_preview_keeps_the_payload_with_synthetic_examples(monkeypatch, has_recipient):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        db.add(ScriptBook(id="example", title="Учебная книга", display_title="Учебная книга",
                          source_filename="example.txt", source_format="txt",
                          voiced_at=datetime(2026, 1, 1)))
        if has_recipient:
            db.add(AudioFile(id="take-example", kind="take", book_code=derive_book_code("Учебная книга"),
                             actor_name="Примерова Мира", role="Другой учебный персонаж",
                             original_filename="example.wav", canonical_filename="example.wav",
                             stored_key="example.wav", mime_type="audio/wav", size_bytes=1, chapter="1"))
        db.commit()
    monkeypatch.setattr(settings, "owner_telegram_id", "900000999")
    monkeypatch.setattr(role_deadlines, "chat_for", lambda _db, _actor: "900000123")
    monkeypatch.setattr(lifecycle_api, "SessionLocal", sessions)
    monkeypatch.setattr(lifecycle_api, "is_authenticated", lambda _request: True)
    monkeypatch.setattr(lifecycle_api, "has_any_role", lambda _request, _allowed: True)

    out = lifecycle_api.api_v2_book_thanks("example", SimpleNamespace())

    assert set(out) == {"ok", "text", "preview", "people", "no_chat"}
    assert out["ok"] is True and out["text"] == book_thanks.DEFAULT_TEXT
    assert out["no_chat"] == []
    assert "«Учебная книга»" in out["preview"]
    assert "{name}" not in out["preview"] and "{roles}" not in out["preview"]
    if has_recipient:
        assert out["preview"].startswith("Мира,")
        assert "Другой учебный персонаж" in out["preview"]
        assert len(out["people"]) == 1
        assert out["people"][0]["actor"] == "Примерова Мира"
    else:
        assert out["preview"].startswith("Лада,")
        assert "Учебный персонаж" in out["preview"]
        assert out["people"] == []
    engine.dispose()
