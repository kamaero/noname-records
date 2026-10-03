"""Дубль главы — послушать и забрать.

Пробу система отдавать умела, дубль — нет: до описи главы до записанного нельзя было
дотянуться иначе как архивом на всю главу целиком.

Слушают и качают дубли все, диктор тоже, и нарочно: услышать, как коллега прочёл
соседнюю роль, — часть работы, а не привилегия. Удаление к этому маршруту отношения не
имеет, оно живёт в своей ручке и своём праве.
"""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.auth import session_serializer
from app.main import app

AUDIO = b"RIFF" + b"\x00" * 996


def _client(roles, name="Роман Сомов"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "u1", "sub": "u1", "roles": roles, "display_name": name}))
    return client


def _take(actor="Роман Сомов", location="local"):
    return SimpleNamespace(
        id="t1", kind="take", role="Сатухух", actor_name=actor, location=location,
        stored_key="КП/Глава04/КП_Глава04_Сатухух_РоманПанков.wav",
        mime_type="audio/wav", size_bytes=len(AUDIO),
        canonical_filename="КП_Глава04_Сатухух_РоманПанков.wav",
    )


def _patch(monkeypatch, *, take=None, tmp_path=None, reads=None):
    monkeypatch.setattr("app.v2.api.find_take", lambda db, audio_id: take)
    monkeypatch.setattr("app.v2.api.audio_size", lambda key, *, location="local": len(AUDIO))
    if tmp_path is not None:
        path = tmp_path / "take.wav"
        path.write_bytes(AUDIO)
        monkeypatch.setattr("app.v2.api.audio_path", lambda key, *, location="local": str(path))

    def read_range(key, start, length, *, location="local"):
        if reads is not None:
            reads.append((start, length))
        return AUDIO[start : start + length]

    monkeypatch.setattr("app.v2.api.read_audio_range", read_range)


class TestWhoMayListen:
    def test_a_stranger_hears_nothing(self):
        assert TestClient(app).get("/api/v2/takes/t1/audio").status_code == 401

    def test_a_dictor_hears_a_colleague(self, monkeypatch, tmp_path):
        """Нарочно: коллегу слушают, чтобы свести прочтение, а не чтобы подсмотреть."""
        _patch(monkeypatch, take=_take("София Ершова"), tmp_path=tmp_path)

        assert _client(["dictor"]).get("/api/v2/takes/t1/audio").status_code == 200

    def test_the_owner_hears_it_whole(self, monkeypatch, tmp_path):
        _patch(monkeypatch, take=_take(), tmp_path=tmp_path)

        response = _client(["admin"]).get("/api/v2/takes/t1/audio")

        assert response.status_code == 200
        assert response.content == AUDIO
        assert response.headers["accept-ranges"] == "bytes"


class TestWhatIsNotATake:
    def test_a_file_that_is_not_there(self, monkeypatch):
        _patch(monkeypatch, take=None)

        assert _client(["author"]).get("/api/v2/takes/t1/audio").status_code == 404

    def test_an_audition_is_not_reachable_by_this_door(self, monkeypatch):
        """У проб свой маршрут. Один и тот же файл не должен иметь двух имён."""
        monkeypatch.setattr("app.v2.api.find_take", lambda db, audio_id: None)

        assert _client(["author"]).get("/api/v2/takes/a1/audio").status_code == 404


class TestSeeking:
    def test_seeking_asks_for_a_slice_and_gets_one(self, monkeypatch):
        _patch(monkeypatch, take=_take())

        response = _client(["author"]).get("/api/v2/takes/t1/audio", headers={"Range": "bytes=100-199"})

        assert response.status_code == 206
        assert response.content == AUDIO[100:200]
        assert response.headers["content-range"] == f"bytes 100-199/{len(AUDIO)}"

    def test_seeking_reads_only_the_slice(self, monkeypatch):
        """Дубль весит и сто двадцать мегабайт; поднимать его целиком ради тысячи
        байт — по файлу в память на каждого слушателя."""
        reads: list = []
        _patch(monkeypatch, take=_take(), reads=reads)

        _client(["author"]).get("/api/v2/takes/t1/audio", headers={"Range": "bytes=100-199"})

        assert reads == [(100, 100)]


class TestTakingItAway:
    def test_listening_shows_the_file_in_place(self, monkeypatch, tmp_path):
        _patch(monkeypatch, take=_take(), tmp_path=tmp_path)

        response = _client(["author"]).get("/api/v2/takes/t1/audio")

        assert response.headers["content-disposition"].startswith("inline;")

    def test_asking_to_download_gets_an_attachment(self, monkeypatch, tmp_path):
        """Та же дверь, другой заголовок: плееру — inline, кнопке «скачать» — attachment."""
        _patch(monkeypatch, take=_take(), tmp_path=tmp_path)

        response = _client(["author"]).get("/api/v2/takes/t1/audio?download=1")

        assert response.headers["content-disposition"].startswith("attachment;")


class TestWhenTheNasIsSilent:
    def test_a_file_still_on_the_nas_is_refused_fast(self, monkeypatch, tmp_path):
        """Мёртвое `hard`-монтирование не падает, а виснет навсегда, съедая поток.
        Честный отказ дешевле повисшей вкладки."""
        from app.services import nas_health

        _patch(monkeypatch, take=_take(location="nas"), tmp_path=tmp_path)
        nas_health.record_probe(False)
        try:
            assert _client(["author"]).get("/api/v2/takes/t1/audio").status_code == 503
        finally:
            nas_health.record_probe(True)

    def test_a_local_file_does_not_care_about_the_nas(self, monkeypatch, tmp_path):
        from app.services import nas_health

        _patch(monkeypatch, take=_take(location="local"), tmp_path=tmp_path)
        nas_health.record_probe(False)
        try:
            assert _client(["author"]).get("/api/v2/takes/t1/audio").status_code == 200
        finally:
            nas_health.record_probe(True)
