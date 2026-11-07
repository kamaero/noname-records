"""Связь очереди, прав и саундтрека без сети и production-хранилища."""
from types import SimpleNamespace
from unittest.mock import Mock
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect

from app.models import AudioFile, SoundtrackEntry, SoundtrackWork, YoutubeSoundtrackDownload
from app.services import youtube_worker
from app.services.soundtrack_store import SoundtrackError
from tests.test_soundtrack import factory, client, change

BASE = "/api/v2/chapters/c1/youtube"
BODY = {"video_id": "abcdefghijk", "scene_id": "scene1", "layer": "music",
        "rights_confirmed": True, "license_note": "CC BY, разрешено коммерческое использование",
        "request_id": "request-1"}


def test_migration_is_idempotent():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0067_youtube_soundtrack.py"
    spec = importlib.util.spec_from_file_location("youtube_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.upgrade()
        assert "youtube_soundtrack_downloads" in inspect(connection).get_table_names()
        module.downgrade()
        module.downgrade()
        assert "youtube_soundtrack_downloads" not in inspect(connection).get_table_names()


@pytest.fixture()
def api(factory, monkeypatch):
    monkeypatch.setattr("app.v2.youtube_api.SessionLocal", factory)
    monkeypatch.setattr("app.services.mirror_wakeup.request_mirror_wakeup", lambda: True)
    queue = Mock()
    known = set()
    queue.fetch_job.side_effect = lambda job_id: object() if job_id in known else None
    queue.enqueue.side_effect = lambda *args, **kwargs: known.add(kwargs["job_id"])
    monkeypatch.setattr("app.workers.launcher.get_queue", lambda name: queue)
    return queue


def test_editor_rights_check_and_idempotent_queue(factory, api):
    with client(None) as user:
        assert user.post(BASE + "/download", json=BODY).status_code == 401
    with client(("dictor",)) as user:
        assert user.post(BASE + "/download", json=BODY).status_code == 403
    with client() as user:
        assert user.post(BASE + "/download", json={**BODY, "rights_confirmed": False}).status_code == 400
        assert user.post(BASE + "/download", json={**BODY, "license_note": ""}).status_code == 400
        first = user.post(BASE + "/download", json=BODY)
        assert first.status_code == 200
        job_id = first.json()["job_id"]
        assert user.post(BASE + "/download", json=BODY).json()["job_id"] == job_id
        assert user.post(BASE + "/download", json={**BODY, "video_id": "ZYXwvutsrqp"}).status_code == 409
        assert user.get(BASE + "/jobs/" + job_id).json()["status"] == "queued"
        assert user.get("/api/v2/chapters/c2/youtube/jobs/" + job_id).status_code == 404
    assert api.enqueue.call_count == 1


def test_retry_after_lost_queue_insertion_reuses_confirmed_job(factory, api):
    with client() as user:
        first = user.post(BASE + "/download", json=BODY).json()
        # Сервер успел сохранить подтверждение, но очередь потеряла задание.
        api.fetch_job.side_effect = lambda _: None
        second = user.post(BASE + "/download", json=BODY).json()
    assert first["job_id"] == second["job_id"]
    assert api.enqueue.call_count == 2
    with factory() as db:
        assert db.query(YoutubeSoundtrackDownload).count() == 1


def test_pending_blocks_finalization_and_locked_chapter_blocks_download(factory, api):
    with client() as user:
        assert user.post(BASE + "/download", json=BODY).status_code == 200
    with pytest.raises(SoundtrackError, match="youtube_download_pending"):
        change(factory, "finalize", confirm_empty=True)
    with factory() as db:
        job = db.query(YoutubeSoundtrackDownload).one()
        job.status = "failed"
        db.commit()
    change(factory, "finalize", confirm_empty=True)
    with client() as user:
        assert user.post(BASE + "/download", json={**BODY, "request_id": "next"}).status_code == 409


def test_rq_failure_settles_running_job_and_redis_outage_does_not(factory, api):
    with client() as user:
        job_id = user.post(BASE + "/download", json=BODY).json()["job_id"]
        with factory() as db:
            db.get(YoutubeSoundtrackDownload, job_id).status = "running"
            db.commit()
        api.fetch_job.side_effect = RuntimeError("redis unavailable")
        assert user.get(BASE + "/jobs/" + job_id).json()["status"] == "running"
        failed = Mock()
        failed.get_status.return_value = "failed"
        api.fetch_job.side_effect = lambda _: failed
        result = user.get(BASE + "/jobs/" + job_id).json()
        assert result["status"] == "failed" and result["error"] == "youtube_worker_interrupted"


def test_finished_queue_job_eventually_releases_abandoned_running_task(factory, api):
    from datetime import timedelta
    from app.time_utils import utcnow_naive
    with client() as user:
        job_id = user.post(BASE + "/download", json=BODY).json()["job_id"]
        with factory() as db:
            row = db.get(YoutubeSoundtrackDownload, job_id)
            row.status = "running"
            row.updated_at = utcnow_naive() - timedelta(hours=4)
            db.commit()
        completed = Mock()
        completed.get_status.return_value = "finished"
        api.fetch_job.side_effect = lambda _: completed
        assert user.get(BASE + "/jobs/" + job_id).json()["status"] == "failed"


def test_worker_saves_manual_provenance_and_unselected_entry(factory, api, monkeypatch):
    with client() as user:
        job_id = user.post(BASE + "/download", json=BODY).json()["job_id"]

    def fake_download(db, **kwargs):
        assert kwargs["rights_confirmed"] is True
        audio = AudioFile(book_code="TEST",
                          original_filename="track.m4a", canonical_filename="track.m4a",
                          stored_key="TEST/Chapter01/track.m4a", mime_type="audio/mp4",
                          size_bytes=10, chapter="1", role="", kind="youtube",
                          duration_seconds=10)
        db.add(audio)
        db.flush()
        return SimpleNamespace(audio_file=audio, provenance={
            "title": "Тестовый трек", "uploader": "Автор", "declared_license": "CC BY",
            "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
        }, cleanup=Mock())

    monkeypatch.setattr("app.services.youtube_download.download_youtube_audio", fake_download)
    wake_states = []
    def wake():
        with factory() as db:
            wake_states.append(db.get(YoutubeSoundtrackDownload, job_id).status)
        return True
    monkeypatch.setattr("app.services.mirror_wakeup.request_mirror_wakeup", wake)
    youtube_worker.perform(factory, job_id)
    youtube_worker.perform(factory, job_id)
    assert wake_states == ["done"]
    with factory() as db:
        job = db.get(YoutubeSoundtrackDownload, job_id)
        assert job.status == "done" and job.audio_file_id
        assert db.query(AudioFile).count() == 1
        entry = db.query(SoundtrackEntry).one()
        work = db.query(SoundtrackWork).one()
        assert not entry.used and entry.scene_id == "scene1" and entry.layer == "music"
        assert work.source == "youtube" and work.original["rights_status"] == "unknown"
        assert job.metadata_json["rights_note"] == BODY["license_note"]


def test_worker_download_error_finishes_job_with_safe_code(factory, api, monkeypatch):
    from app.services.youtube_download import YoutubeDownloadError
    with client() as user:
        job_id = user.post(BASE + "/download", json=BODY).json()["job_id"]
    def fail(*args, **kwargs):
        raise YoutubeDownloadError("youtube_too_long")
    monkeypatch.setattr("app.services.youtube_download.download_youtube_audio", fail)
    youtube_worker.perform(factory, job_id)
    with factory() as db:
        job = db.get(YoutubeSoundtrackDownload, job_id)
        assert job.status == "failed" and job.error_code == "youtube_too_long"


def test_repeated_selection_does_not_erase_used_marker(factory, api, monkeypatch):
    with client() as user:
        first = user.post(BASE + "/download", json=BODY).json()["job_id"]
    def fake_download(db, **kwargs):
        audio = AudioFile(book_code="TEST",
                          original_filename="track.m4a", canonical_filename="track.m4a",
                          stored_key=kwargs["task_id"], mime_type="audio/mp4",
                          size_bytes=10, chapter="1", role="", kind="youtube", duration_seconds=10)
        db.add(audio)
        db.flush()
        return SimpleNamespace(audio_file=audio, provenance={"title": "Трек", "source_url": "https://youtube.com/"},
                               cleanup=Mock())
    monkeypatch.setattr("app.services.youtube_download.download_youtube_audio", fake_download)
    youtube_worker.perform(factory, first)
    with factory() as db:
        entry = db.query(SoundtrackEntry).one()
        entry.used = True
        entry.overrides = {**entry.overrides, "title": "Название после ручной правки"}
        db.commit()
    with client() as user:
        second = user.post(BASE + "/download", json={**BODY, "request_id": "request-2"}).json()["job_id"]
    youtube_worker.perform(factory, second)
    with factory() as db:
        entry = db.query(SoundtrackEntry).one()
        assert entry.used is True and entry.overrides["title"] == "Название после ручной правки"


def test_same_video_in_two_chapters_keeps_each_download_file_and_rights(factory, api, monkeypatch):
    from app.models import SoundMarker
    from app.services.soundtrack_store import chapter_payload
    from app.v2.youtube_api import _create
    with factory() as db:
        db.add(SoundMarker(id="scene-other", book_id="b1", chapter_id="c2", kind="scene"))
        db.commit()
    first = _create("c1", {**BODY, "license_note": "First chapter permission"}, "first-editor")
    second = _create("c2", {**BODY, "scene_id": "scene-other", "license_note": "Second chapter permission"}, "second-editor")
    import json
    ids = [json.loads(response.body)["job_id"] for response in (first, second)]
    def fake_download(db, **kwargs):
        audio = AudioFile(book_code="TEST", chapter=kwargs["chapter_id"], role="", kind="youtube", original_filename="track.m4a",
                          stored_key=kwargs["task_id"], mime_type="audio/mp4", size_bytes=10,
                          duration_seconds=10 if kwargs["chapter_id"] == "c1" else 12)
        db.add(audio)
        db.flush()
        return SimpleNamespace(audio_file=audio, provenance={"title": "Synthetic track"}, cleanup=Mock())
    monkeypatch.setattr("app.services.youtube_download.download_youtube_audio", fake_download)
    for job_id in ids:
        youtube_worker.perform(factory, job_id)
    with factory() as db:
        assert db.query(SoundtrackWork).count() == 1
        for chapter, job_id, editor, note in (("c1", ids[0], "first-editor", "First chapter permission"),
                                              ("c2", ids[1], "second-editor", "Second chapter permission")):
            entry = chapter_payload(db, chapter)["entries"][0]
            job = db.get(YoutubeSoundtrackDownload, job_id)
            assert entry["audio_file_id"] == job.audio_file_id
            assert entry["confirmed_by"] == editor
            assert entry["rights_confirmation"] == note
        assert chapter_payload(db, "c1")["entries"][0]["audio_file_id"] != chapter_payload(db, "c2")["entries"][0]["audio_file_id"]
