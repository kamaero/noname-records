"""Экран проб: список у роли и сама запись.

Прослушивания в системе не было вовсе — ни одного `<audio>`, ни одной отдачи файла.
Поэтому здесь два маршрута: что прислали и как это услышать.

Слушают пробы все — диктор тоже: услышать, как роль звучит в чужом исполнении, полезно
всем, кто книгу читает. Утверждает на роль только владелец или автор, и это решается на
сервере, а не спрятанной кнопкой.
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


def _audition(actor="Роман Сомов"):
    return SimpleNamespace(
        id="a1", kind="audition", role="Сатухух", actor_name=actor,
        stored_key="uploads/raw/a1.wav", mime_type="audio/wav", size_bytes=len(AUDIO),
        canonical_filename="КП_Проба_Сатухух_РоманПанков.wav",
    )


def _patch(monkeypatch, *, audition=None, rows=None, tmp_path=None, reads=None):
    monkeypatch.setattr("app.v2.api.attach_reactions", lambda db, rows, **_: rows)
    monkeypatch.setattr("app.v2.api.book_auditions", lambda db, *, book_id, only_actor="": rows)
    monkeypatch.setattr("app.v2.api.find_audition", lambda db, audio_id: audition)
    monkeypatch.setattr("app.v2.api.audio_size", lambda key, *, location="nas": len(AUDIO))
    if tmp_path is not None:
        path = tmp_path / "audition.wav"
        path.write_bytes(AUDIO)
        monkeypatch.setattr("app.v2.api.audio_path", lambda key, *, location="nas": str(path))

    def read_range(key, start, length, *, location="nas"):
        if reads is not None:
            reads.append((start, length))
        return AUDIO[start : start + length]

    monkeypatch.setattr("app.v2.api.read_audio_range", read_range)


class TestTheList:
    def test_a_stranger_gets_nothing(self):
        assert TestClient(app).get("/api/v2/books/b1/auditions").status_code == 401

    def test_a_missing_book_is_missing(self, monkeypatch):
        _patch(monkeypatch, rows=None)

        assert _client(["author"]).get("/api/v2/books/b1/auditions").status_code == 404

    def test_everyone_hears_every_audition(self, monkeypatch):
        _patch(monkeypatch, rows=[{"id": "a1", "role": "Сатухух", "actor_name": "София Ершова"}])

        for role in ("admin", "author", "dictor"):
            body = _client([role]).get("/api/v2/books/b1/auditions").json()
            assert len(body["items"]) == 1

    def test_the_answer_says_who_may_approve(self, monkeypatch):
        """Кнопка «Назначить на роль» показывается по этому флагу — и по нему же охраняется."""
        _patch(monkeypatch, rows=[])

        assert _client(["admin"]).get("/api/v2/books/b1/auditions").json()["can_approve"] is True
        assert _client(["author"]).get("/api/v2/books/b1/auditions").json()["can_approve"] is True
        assert _client(["dictor"]).get("/api/v2/books/b1/auditions").json()["can_approve"] is False


class TestListeningToOne:
    def test_a_stranger_hears_nothing(self):
        assert TestClient(app).get("/api/v2/auditions/a1/audio").status_code == 401

    def test_a_file_that_is_not_there(self, monkeypatch):
        _patch(monkeypatch, audition=None)

        assert _client(["author"]).get("/api/v2/auditions/a1/audio").status_code == 404

    def test_the_author_hears_it_whole(self, monkeypatch, tmp_path):
        _patch(monkeypatch, audition=_audition(), tmp_path=tmp_path)

        response = _client(["author"]).get("/api/v2/auditions/a1/audio")

        assert response.status_code == 200
        assert response.content == AUDIO
        assert response.headers["accept-ranges"] == "bytes"

    def test_a_dictor_hears_somebody_elses_too(self, monkeypatch, tmp_path):
        """Слушать пробу на роль полезно каждому, кто книгу читает; утверждает — не каждый."""
        _patch(monkeypatch, audition=_audition("София Ершова"), tmp_path=tmp_path)

        assert _client(["dictor"]).get("/api/v2/auditions/a1/audio").status_code == 200

    def test_seeking_asks_for_a_slice_and_gets_one(self, monkeypatch):
        reads: list = []
        _patch(monkeypatch, audition=_audition(), reads=reads)

        response = _client(["author"]).get("/api/v2/auditions/a1/audio", headers={"Range": "bytes=100-199"})

        assert response.status_code == 206
        assert response.content == AUDIO[100:200]
        assert response.headers["content-range"] == f"bytes 100-199/{len(AUDIO)}"

    def test_seeking_reads_only_the_slice(self, monkeypatch):
        """Проба весит 80 МБ; поднимать её целиком ради тысячи байт — по файлу на слушателя."""
        reads: list = []
        _patch(monkeypatch, audition=_audition(), reads=reads)

        _client(["author"]).get("/api/v2/auditions/a1/audio", headers={"Range": "bytes=100-199"})

        assert reads == [(100, 100)]
