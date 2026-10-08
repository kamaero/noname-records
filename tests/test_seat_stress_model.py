"""Модель ударений в настольной версии — только по кнопке: 700 МБ без спроса выглядели бы как
зависание первой разметки."""
import threading

import pytest

from app.config import settings
from app.services import stress_model
from app.v2 import stress_ruaccent


@pytest.fixture(autouse=True)
def _one(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "seat_mode", "one")
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(stress_model, "_STATE", {"state": "", "error": ""})
    monkeypatch.setattr(stress_ruaccent, "_INSTANCE", None)
    monkeypatch.setattr(stress_ruaccent, "_UNAVAILABLE", False)


def test_absent_until_downloaded_and_markup_skips_the_layer():
    assert stress_model.state()["state"] == "absent"
    assert stress_ruaccent.get_ruaccent() is None
    assert stress_ruaccent._UNAVAILABLE is False  # не навсегда: скачают — заработает


def test_download_marks_ready_only_after_success():
    def loader(workdir):
        (stress_model.model_dir() / "nn").mkdir(parents=True)
        (stress_model.model_dir() / "nn" / "w.bin").write_bytes(b"x" * 2_000_000)
    stress_model.download(loader=loader)
    assert stress_model.state()["state"] == "ready"
    assert stress_model.state()["downloaded_mb"] >= 1


def test_a_failed_download_is_reported_and_not_ready():
    def loader(workdir):
        (stress_model.model_dir() / "half.bin").write_bytes(b"x")
        raise OSError("нет сети")
    stress_model.download(loader=loader)
    body = stress_model.state()
    assert body["state"] == "failed" and "нет сети" in body["error"]
    assert not stress_model.is_ready()


def test_a_second_press_while_downloading_does_not_start_another(monkeypatch):
    gate, calls = threading.Event(), []

    def loader(workdir):
        calls.append(1)
        gate.wait(5)
    first = stress_model.start_download(loader=loader)
    assert stress_model.state()["state"] == "downloading"
    assert stress_model.start_download(loader=loader) is None
    gate.set()
    first.join(5)
    assert calls == [1] and stress_model.state()["state"] == "ready"


def test_studio_keeps_the_old_automatic_behaviour(monkeypatch):
    monkeypatch.setattr(settings, "seat_mode", "studio")
    assert stress_model.state()["state"] == "auto"


def _owner_client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.auth import session_serializer
    from app.main import app
    from app.seat import seat_fingerprint
    monkeypatch.setattr(settings, "seat_token", "k" * 48)
    client = TestClient(app, base_url="http://127.0.0.1:5000")
    client.cookies.set("session", session_serializer.dumps(
        {"uid": "local-owner", "sub": "owner", "roles": ["admin"], "seat": seat_fingerprint()}))
    return client


def test_routes(monkeypatch):
    client = _owner_client(monkeypatch)
    started = threading.Event()
    monkeypatch.setattr(stress_model, "_default_loader", lambda workdir: started.set())
    assert client.get("/api/settings/stress-model").json()["state"] == "absent"
    response = client.post("/api/settings/stress-model")
    assert response.status_code == 200 and response.json()["state"] in ("downloading", "ready")
    assert started.wait(5)


def test_routes_refuse_in_studio_and_without_admin(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    assert TestClient(app, base_url="http://127.0.0.1:5000").get("/api/settings/stress-model").status_code == 401
    monkeypatch.setattr(settings, "seat_mode", "studio")
    from app.auth import session_serializer
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "a", "sub": "a", "roles": ["admin"]}))
    assert client.get("/api/settings/stress-model").json()["state"] == "auto"
    assert client.post("/api/settings/stress-model").status_code == 409
