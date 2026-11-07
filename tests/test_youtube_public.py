"""Новая публичная цепочка сохраняет язык, типизированные роли и локальную очередь."""
from datetime import timedelta
import importlib.util
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.config import settings
from app.db import Base
from app.models import AudioFile, ScriptBook, ScriptChapter, YoutubeSoundtrackDownload
from app.time_utils import utcnow_naive
from tests.test_soundtrack import factory


def job(identity, status="queued", old=False):
    return YoutubeSoundtrackDownload(id=identity, book_id="b1", chapter_id="c1", scene_id="scene1",
        layer="music", video_id="abcdefghijk", request_id=identity, rights_note="Synthetic permission",
        confirmed_by="u1", status=status,
        updated_at=utcnow_naive() - timedelta(hours=4 if old else 0))


def test_music_migrations_follow_current_language_chain_and_preserve_existing_data(tmp_path, monkeypatch):
    import app.db
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    chain = ("0065_ambient_language_snapshot", "0066_soundtrack", "0067_youtube_soundtrack")
    for previous, current in zip(chain, chain[1:]):
        assert scripts.get_revision(current).down_revision == previous
    engine = create_engine("sqlite:///" + str(tmp_path / "public.db"))
    new_tables = {name for name in Base.metadata.tables if name.startswith("soundtrack_")} | {"youtube_soundtrack_downloads"}
    Base.metadata.create_all(engine, tables=[table for name, table in Base.metadata.tables.items() if name not in new_tables])
    with engine.begin() as conn:
        conn.execute(ScriptBook.__table__.insert().values(id="preserve", title="Synthetic book", language="en",
            role_identity_mode="typed_v1", source_filename="source.txt", source_format="txt"))
        conn.execute(ScriptChapter.__table__.insert().values(id="old-chapter", book_id="preserve", chapter_index=1,
            chapter_title="Chapter 1", status="published"))
        conn.execute(AudioFile.__table__.insert().values(id="old-take", book_code="SB", chapter="Chapter 1",
            role="Author", role_book_id="preserve", role_ref="character:author-character", actor_name="Synthetic actor",
            original_filename="take.wav", stored_key="fixture/take.wav", mime_type="audio/wav", size_bytes=12))
        before = {name: dict(conn.execute(text(f"SELECT * FROM {name}")).mappings().one())
            for name in ("script_books", "script_chapters", "audio_files")}
    monkeypatch.setattr(app.db, "engine", engine)
    monkeypatch.setattr(settings, "database_url", str(engine.url))
    command.stamp(config, chain[0])
    for revision in chain[1:]:
        command.upgrade(config, revision)
    with engine.begin() as conn:
        assert new_tables <= set(inspect(conn).get_table_names())
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == chain[-1]
        for name, original in before.items():
            assert dict(conn.execute(text(f"SELECT * FROM {name}")).mappings().one()) == original
        for name in chain[1:]:
            path = Path("alembic/versions") / (name + ".py")
            spec = importlib.util.spec_from_file_location(name, path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                migration.upgrade()
        for name, original in before.items():
            assert dict(conn.execute(text(f"SELECT * FROM {name}")).mappings().one()) == original
    engine.dispose()


def test_one_seat_enqueue_executes_keyword_task_without_redis(monkeypatch):
    from app.v2.youtube_api import _enqueue
    from app.workers.local_queue import LocalQueue
    import app.worker_tasks
    queue, calls = LocalQueue("youtube-test"), []
    monkeypatch.setattr(settings, "seat_mode", "one")
    monkeypatch.setattr("app.workers.launcher.get_queue", lambda _: queue)
    monkeypatch.setattr(app.worker_tasks, "perform_youtube_soundtrack_task", lambda download_id: calls.append(download_id))
    _enqueue("download-1")
    assert queue.join(5)
    assert calls == ["download-1"]


def test_one_seat_retry_preserves_confirmed_row_after_queue_loses_task(factory, monkeypatch):
    from app.v2.youtube_api import _create
    from tests.test_youtube_integration import BODY
    class Queue:
        def __init__(self):
            self.calls = []
        def enqueue(self, reference, *, download_id, job_timeout, result_ttl):
            self.calls.append(download_id)
    queue = Queue()
    monkeypatch.setattr(settings, "seat_mode", "one")
    monkeypatch.setattr("app.v2.youtube_api.SessionLocal", factory)
    monkeypatch.setattr("app.workers.launcher.get_queue", lambda _: queue)
    assert _create("c1", BODY, "u1").status_code == 200
    assert _create("c1", BODY, "u1").status_code == 200
    assert len(queue.calls) == 2 and queue.calls[0] == queue.calls[1]
    with factory() as db:
        assert db.query(YoutubeSoundtrackDownload).count() == 1


def test_one_seat_diagnosis_does_not_call_rq_or_fail_waiting_queue(factory, monkeypatch):
    from app.v2.youtube_api import _settle_lost
    monkeypatch.setattr(settings, "seat_mode", "one")
    monkeypatch.setattr("app.workers.launcher.get_queue", lambda _: (_ for _ in ()).throw(AssertionError("RQ called")))
    with factory() as db:
        db.add_all([job("waiting", old=True), job("recent", status="running"), job("dead", status="running", old=True)])
        db.commit()
        for identity in ("waiting", "recent", "dead"):
            _settle_lost(db, db.get(YoutubeSoundtrackDownload, identity))
        assert db.get(YoutubeSoundtrackDownload, "waiting").status == "queued"
        assert db.get(YoutubeSoundtrackDownload, "recent").status == "running"
        assert db.get(YoutubeSoundtrackDownload, "dead").status == "failed"


def test_desktop_restart_releases_lost_youtube_jobs_and_preserves_finished(factory):
    from app.seat import mark_interrupted_runs
    with factory() as db:
        db.add_all([job("waiting"), job("running", status="running"), job("finished", status="done")])
        db.commit()
        assert mark_interrupted_runs(db) == 2
        assert db.get(YoutubeSoundtrackDownload, "waiting").status == "failed"
        assert db.get(YoutubeSoundtrackDownload, "running").error_code == "youtube_worker_interrupted"
        assert db.get(YoutubeSoundtrackDownload, "finished").status == "done"


def test_public_player_csp_only_adds_official_frame_host():
    from app.main import CONTENT_SECURITY_POLICY
    directives = dict(part.strip().split(" ", 1) for part in CONTENT_SECURITY_POLICY.split(";"))
    assert directives["frame-src"] == "https://oauth.telegram.org https://telegram.org https://www.youtube-nocookie.com"
    assert directives["frame-ancestors"] == "'none'"
