"""Готовность главы видят все, забирает исходник — тот, кто сводит.

Диктору полезно видеть, что глава ждёт только его: это точнее любого напоминания.
Архив с исходниками — материал для сведения, и он не для всех.
"""
import pytest
from fastapi.testclient import TestClient

from app.auth import session_serializer
from app.main import app


def _client(roles, name="Кто-то"):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "u1", "sub": "u1", "roles": list(roles), "display_name": name}))
    return client


@pytest.fixture()
def status(monkeypatch):
    payload = {
        "chapter_id": "ch-1", "chapter_index": 4, "chapter_title": "Проба пера",
        "roles": [], "total_roles": 2, "recorded_roles": 1, "ready": False,
        "duration_seconds": 0.0, "size_bytes": 0,
    }
    monkeypatch.setattr("app.v2.api.chapter_recording_status", lambda db, chapter_id: None if chapter_id == "missing" else payload)
    return payload


class TestSeeingWhatIsMissing:
    def test_a_stranger_sees_nothing(self):
        assert TestClient(app).get("/api/v2/chapters/ch-1/recording").status_code == 401

    def test_a_dictor_sees_it(self, status):
        body = _client(["dictor"]).get("/api/v2/chapters/ch-1/recording").json()

        assert body["recorded_roles"] == 1 and body["total_roles"] == 2

    def test_but_is_not_offered_the_archive(self, status):
        assert _client(["dictor"]).get("/api/v2/chapters/ch-1/recording").json()["can_download"] is False
        assert _client(["author"]).get("/api/v2/chapters/ch-1/recording").json()["can_download"] is True

    def test_a_chapter_that_does_not_exist(self, status):
        assert _client(["author"]).get("/api/v2/chapters/missing/recording").status_code == 404


class TestTakingTheArchive:
    def test_a_stranger_gets_nothing(self):
        assert TestClient(app).get("/api/v2/chapters/ch-1/archive.zip").status_code == 401

    def test_a_dictor_is_turned_away(self, monkeypatch):
        monkeypatch.setattr("app.v2.api.build_chapter_archive", lambda db, chapter_id, target: {})

        assert _client(["dictor"]).get("/api/v2/chapters/ch-1/archive.zip").status_code == 403

    def test_the_owner_gets_the_file(self, monkeypatch, tmp_path):
        import zipfile

        def fake_build(db, chapter_id, target):
            with zipfile.ZipFile(target, "w") as archive:
                archive.writestr("опись.txt", "Крылья полумрака")
            return {"archive_name": "KP_Ch04.zip", "chapter_index": 4, "files_written": 1, "files_missing": []}

        monkeypatch.setattr("app.v2.api.build_chapter_archive", fake_build)
        # Сама целостность файлов главы здесь не проверяется — это отдельный тест
        # ниже; тут читаем реальную БД без схемы, поэтому вердикт подменяется.
        monkeypatch.setattr("app.v2.api.chapter_has_broken_files", lambda db, chapter_id: False)

        response = _client(["admin"]).get("/api/v2/chapters/ch-1/archive.zip")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert "KP_Ch04.zip" in response.headers.get("content-disposition", "")
        assert response.content.startswith(b"PK")

    def test_a_chapter_that_does_not_exist(self, monkeypatch):
        monkeypatch.setattr("app.v2.api.build_chapter_archive", lambda db, chapter_id, target: None)
        monkeypatch.setattr("app.v2.api.chapter_has_broken_files", lambda db, chapter_id: False)

        assert _client(["admin"]).get("/api/v2/chapters/missing/archive.zip").status_code == 404

    def test_a_chapter_with_broken_files_is_refused(self, monkeypatch):
        """Заглушка тут доказывает только проводку маршрута — саму логику
        предиката проверяют модульные тесты в test_audio_integrity.py. Собирать
        архив на диск и тут же выбрасывать его — это минуты на главу в сотни
        мегабайт, поэтому `build_chapter_archive` не должен вызываться вовсе."""
        called = []
        monkeypatch.setattr("app.v2.api.chapter_has_broken_files", lambda db, chapter_id: True)
        monkeypatch.setattr(
            "app.v2.api.build_chapter_archive",
            lambda db, chapter_id, target: called.append(chapter_id) or {},
        )

        response = _client(["admin"]).get("/api/v2/chapters/ch-1/archive.zip")

        assert response.status_code == 409
        assert response.json()["error"] == "integrity_failed"
        assert called == []


def test_an_archive_that_needs_a_silent_nas_is_a_fast_503(monkeypatch):
    """Сборка отказалась трогать NAS — владелец видит «хранилище недоступно», тот же
    ответ, что у прослушивания, а не повисшую загрузку и не пустой архив."""
    from app.services.chapter_delivery import StorageUnavailable

    def refusing_build(db, chapter_id, target):
        raise StorageUnavailable(chapter_id)

    monkeypatch.setattr("app.v2.api.build_chapter_archive", refusing_build)
    monkeypatch.setattr("app.v2.api.chapter_has_broken_files", lambda db, chapter_id: False)

    response = _client(["admin"]).get("/api/v2/chapters/ch-1/archive.zip")

    assert response.status_code == 503
    assert response.json()["error"] == "storage_unavailable"


def test_taking_the_archive_records_it(monkeypatch, tmp_path):
    """Отметка «сдана» должна пережить закрытие сессии, а не только дожить до конца вызова.

    Обработчик открывал сессию, звал сборку и выходил, ничего не зафиксировав: глава
    помечалась и тут же откатывалась. Тест на объекте этого не видел — он смотрел ту же
    сессию, в которой отметку и поставили.
    """
    import zipfile

    marks = []

    def fake_build(db, chapter_id, target):
        marks.append(chapter_id)
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr("опись.txt", "х")
        return {"archive_name": "KP_Ch04.zip", "chapter_index": 4, "files_written": 1, "files_missing": []}

    committed = []
    monkeypatch.setattr("app.v2.api.build_chapter_archive", fake_build)
    monkeypatch.setattr("app.v2.api.chapter_has_broken_files", lambda db, chapter_id: False)

    import app.v2.api as api

    real_session = api.SessionLocal

    class _Watching:
        def __init__(self):
            self._db = real_session()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._db.__exit__(*exc) if hasattr(self._db, "__exit__") else self._db.close()
            return False

        def commit(self):
            committed.append(True)

        def __getattr__(self, name):
            return getattr(self._db, name)

    monkeypatch.setattr(api, "SessionLocal", _Watching)

    assert _client(["admin"]).get("/api/v2/chapters/ch-1/archive.zip").status_code == 200
    assert committed, "обработчик обязан зафиксировать отметку о сдаче"


class TestAskingForAVerification:
    """Сверка главы — многоминутное перечитывание с NAS в единственной очереди
    `high`. Ставить его в очередь может тот же круг, что заказывает распознавание и
    забирает архив: у соседних ручек `_can_edit` есть, у этой не было."""

    def test_a_stranger_gets_nothing(self):
        assert TestClient(app).post("/api/v2/chapters/ch-1/verify").status_code == 401

    def test_a_dictor_is_turned_away(self, monkeypatch):
        called: list[str] = []
        monkeypatch.setattr("app.v2.api.enqueue_verify_for_chapter", lambda chapter_id: called.append(chapter_id))

        assert _client(["dictor"]).post("/api/v2/chapters/ch-1/verify").status_code == 403
        assert called == [], "диктор занял очередь `high` многоминутной сверкой"

    def test_the_owner_may_ask_for_it(self, monkeypatch):
        monkeypatch.setattr("app.v2.api.enqueue_verify_for_chapter", lambda chapter_id: "job-1")

        response = _client(["admin"]).post("/api/v2/chapters/ch-1/verify")

        assert response.status_code == 200 and response.json()["job_id"] == "job-1"
