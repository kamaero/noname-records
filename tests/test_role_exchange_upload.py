"""Аудио внутрь: в «одном месте» режиссёр грузит присланное актёром — это дубль.

Проб там нет (они скрыты), а сервер решал «проба или дубль» по касту: не утверждённый
актёр, «Имя?», рассказчик из сметы — и файл молча становился пробой без сверки.
"""
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.upload_kind import classify_upload_kind
from tests.test_audition_batch_endpoint import _meta, _post, _sessions, _upload


def test_one_seat_files_a_take_for_a_role_nobody_approved(monkeypatch):
    monkeypatch.setattr(settings, "seat_mode", "one")

    status, body, stored, _ = _post([_upload("x.wav")], [_meta("Сатухух", "Глава 4")])

    assert status == 200 and body["ok"] is True
    assert [(kind, role, chapter) for kind, role, chapter, _ in stored] == [("take", "Сатухух", "Глава 4")]


def test_a_take_still_needs_its_chapter(monkeypatch):
    monkeypatch.setattr(settings, "seat_mode", "one")

    status, body, stored, _ = _post([_upload("x.wav")], [_meta("Сатухух")])

    assert status >= 400 and stored == []
    assert any(item["error"] == "chapter_required" for item in body["items"])


def test_the_studio_still_tells_an_audition_from_a_take():
    with _sessions()() as db:
        assert classify_upload_kind(db, role="Сатухух", actor_name="Роман Сомов") == "audition"


def test_the_preview_has_an_address_one_seat_does_not_hide(monkeypatch):
    monkeypatch.setattr(settings, "seat_mode", "one")
    client = TestClient(app, base_url="http://127.0.0.1:5000")

    response = client.post("/api/recording/batch-validate", json={"files": []})

    assert response.status_code == 401  # дошло до ручки: без сессии — отказ, а не «скрыто»
    assert client.post("/dictor-pro/batch-validate", json={"files": []}).json()["error"] == "not_in_one_seat"
