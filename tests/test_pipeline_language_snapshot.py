"""Очередь фиксирует язык до оплаты; смена профиля не переписывает незавершённый run."""
import importlib.util
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table, create_engine, inspect
from sqlalchemy.orm import sessionmaker

from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import BackgroundRun, ScriptBook, ScriptChapter
from app.v2.models import V2Run


@pytest.fixture
def queued(tmp_path, monkeypatch):
    from app import worker_tasks
    from app.v2 import api
    from app.workers import launcher

    engine = create_engine(f"sqlite:///{tmp_path / 'profile.db'}", connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for name in ("app.db.SessionLocal", "app.v2.api.SessionLocal", "app.worker_tasks.SessionLocal"):
        monkeypatch.setattr(name, factory)
    monkeypatch.setattr(api.spend, "estimate_markup", lambda *a, **k: (0, False))
    monkeypatch.setattr(api, "limit_gate", lambda *a, **k: None)
    jobs = []

    class Queue:
        def enqueue(self, func, **kwargs):
            jobs.append((func, {k: v for k, v in kwargs.items() if k not in {"job_timeout", "result_ttl"}}))
            return SimpleNamespace(id=kwargs["run_id"])

    monkeypatch.setattr(launcher, "get_queue", lambda name: Queue())
    with factory() as db:
        db.add(ScriptBook(id="book", title="Source title", source_filename="source.txt", source_format="txt", language="en"))
        db.add(ScriptChapter(id="chapter", book_id="book", chapter_index=1, chapter_title="Source heading",
                             source_text='“Hello,” Aster said.'))
        db.commit()
    yield factory, jobs
    engine.dispose()


def client():
    instance = TestClient(app)
    instance.cookies.set("session", session_serializer.dumps({"uid": "editor", "sub": "Test Editor", "roles": ["admin"]}))
    return instance


def set_language(factory, value):
    with factory() as db:
        db.get(ScriptBook, "book").language = value
        db.commit()


def run_job(job):
    from app import worker_tasks
    function, kwargs = job
    return getattr(worker_tasks, function.rsplit(".", 1)[1])(**kwargs)


@pytest.mark.parametrize("original,changed", [("en", "ru"), ("ru", "en")])
@pytest.mark.parametrize("restored", [False, True])
def test_real_queue_to_worker_uses_saved_profile_without_mutating_book(queued, monkeypatch, original, changed, restored):
    from app.v2 import pipeline
    from app.services import char_extraction

    factory, jobs = queued
    set_language(factory, original)
    captures = []
    resources = []
    monkeypatch.setattr("app.pipeline.llm_client._resolve_provider", lambda name: ("key", "unused", "openai"))
    monkeypatch.setattr(char_extraction, "_resolve_provider", lambda name: ("key", "unused", "openai"))

    def extract(base, key, model, system, user, **kwargs):
        assert client().post("/api/v2/books/book/language", json={"language": original}).status_code == 409
        captures.append(("cast", system, user))
        return {"content": json.dumps({"characters": [{"canonical_name": "Aster", "aliases": [],
                 "appears_in_chapters": [1]}], "book_synopsis": ""})}

    def llm(system, user, schema):
        captures.append(("attribute", system, user))
        ids = re.findall(r"^#\d{5}", user, re.M)
        return {"items": [{"id": item, "speaker": "Aster", "confidence": 1.0} for item in ids]}

    monkeypatch.setattr(char_extraction, "call_chat", extract)
    monkeypatch.setattr("app.v2.llm.make_llm", lambda *a, **k: llm)
    monkeypatch.setattr(pipeline, "_ruaccent_layer", lambda: resources.append("ruaccent") or None)
    monkeypatch.setattr(pipeline.Resolver, "default", lambda **k: resources.append("dictionary") or pipeline.Resolver())
    monkeypatch.setattr(pipeline, "_settle_book", lambda *a: None)
    monkeypatch.setattr(pipeline, "_notify", lambda *a: None)
    response = client().post("/api/v2/books/book/run", json={"force": True})
    assert response.status_code == 200, response.text
    rid = response.json()["run_id"]
    with factory() as db:
        assert db.get(V2Run, rid).language_snapshot == original
        background = db.get(BackgroundRun, jobs[0][1]["run_id"])
        assert json.loads(background.meta_json)["language"] == original
    refused = client().post("/api/v2/books/book/language", json={"language": changed})
    assert refused.status_code == 409 and refused.json()["error"] == "run_in_progress"
    assert captures == []
    # Независимая запись обходит HTTP guard: immutable run всё равно обязан удержать профиль.
    set_language(factory, changed)
    if restored:
        jobs[0][1].pop("language")
    run_job(jobs[0])
    assert {item[0] for item in captures} == {"cast", "attribute"}
    cast = next(item for item in captures if item[0] == "cast")
    if original == "en":
        assert "Language: English" in cast[2] and "Chapter 1" in cast[2]
        assert resources == []
    else:
        assert "Режим: char_extraction" in cast[2] and "Глава 1" in cast[2]
        assert resources == ["ruaccent", "dictionary"]
    with factory() as db:
        assert db.get(V2Run, rid).status == "done"
        assert db.get(ScriptBook, "book").language == changed
        assert db.get(ScriptChapter, "chapter").source_text == '“Hello,” Aster said.'
        assert json.loads(db.get(BackgroundRun, jobs[0][1]["run_id"]).meta_json)["language"] == original


@pytest.mark.parametrize("original,changed", [("en", "ru"), ("ru", "en")])
@pytest.mark.parametrize("kind", ["consilium", "sound", "ambient"])
def test_other_queued_models_keep_profile_and_block_switch(queued, monkeypatch, original, changed, kind):
    from app.services import ambient_engine, consilium_engine, sound_engine

    factory, jobs = queued
    set_language(factory, original)
    callbacks = []
    engines = {"consilium": consilium_engine, "sound": sound_engine, "ambient": ambient_engine}
    engine = engines[kind]
    if kind == "ambient":
        engine.enqueue_ambient("chapter")
    else:
        getattr(engine, "enqueue_" + kind)("book", "recheck" if kind == "consilium" else "rest")
    assert len(jobs) == 1 and jobs[0][1]["language"] == original
    with factory() as db:
        assert json.loads(db.get(BackgroundRun, jobs[0][1]["run_id"]).meta_json)["language"] == original
    assert client().post("/api/v2/books/book/language", json={"language": changed}).status_code == 409
    set_language(factory, changed)
    monkeypatch.setattr(engine, "run_" + kind, lambda **kwargs: callbacks.append(kwargs))
    jobs[0][1].pop("language")
    run_job(jobs[0])
    assert len(callbacks) == 1 and callbacks[0]["language"] == original
    with factory() as db:
        assert db.get(ScriptBook, "book").language == changed


@pytest.mark.parametrize("kind", ["v2_pipeline", "consilium", "sound", "ambient"])
def test_legacy_pending_jobs_without_a_proven_snapshot_never_call_models(queued, monkeypatch, kind):
    from app import worker_tasks
    from app.services import ambient_engine, consilium_engine, sound_engine
    from app.v2 import pipeline

    factory, _ = queued
    with factory() as db:
        db.add(BackgroundRun(id="legacy-job", job_kind=kind, entity_type="script_book", entity_id="book",
                             run_key=f"{kind}:book", status="queued", meta_json="{}"))
        db.add(V2Run(id="legacy-run", book_id="book", status="queued"))
        db.commit()
    monkeypatch.setattr(pipeline.RunContext, "get_llm", lambda *a: pytest.fail("paid model"))
    for engine in [consilium_engine, sound_engine, ambient_engine]:
        monkeypatch.setattr(engine, "routed_ask", lambda *a: pytest.fail("paid model"), raising=False)
    kwargs = {"chapter_id": "chapter"} if kind == "ambient" else {"book_id": "book"}
    if kind == "v2_pipeline":
        kwargs["v2_run_id"] = "legacy-run"
    function = getattr(worker_tasks, "perform_" + ("v2_pipeline" if kind == "v2_pipeline" else kind) + "_task")
    with pytest.raises(RuntimeError, match="language_snapshot_missing"):
        function(run_id="legacy-job", **kwargs)
    with factory() as db:
        assert db.get(BackgroundRun, "legacy-job").status == "failed"
        assert db.get(ScriptBook, "book").language == "en"


def test_queue_and_language_change_serialize_at_the_database_boundary(queued, monkeypatch):
    from app.v2 import api

    factory, _ = queued
    locked, release, change_done = threading.Event(), threading.Event(), threading.Event()
    def estimate(*args, **kwargs):
        locked.set()
        assert release.wait(5)
        return 0, False
    monkeypatch.setattr(api.spend, "estimate_markup", estimate)
    def switch():
        try:
            return client().post("/api/v2/books/book/language", json={"language": "ru"})
        finally:
            change_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(lambda: client().post("/api/v2/books/book/run", json={"steps": ["segment"]}))
        assert locked.wait(5)
        second = pool.submit(switch)
        try:
            assert not change_done.wait(0.1)
        finally:
            release.set()
        assert first.result(5).status_code == 200
        assert second.result(5).status_code == 409
    with factory() as db:
        assert db.query(V2Run).one().language_snapshot == "en"
        assert db.get(ScriptBook, "book").language == "en"


def test_language_change_winning_the_lock_is_the_next_queue_snapshot(queued, monkeypatch):
    from app.v2 import api

    factory, _ = queued
    set_language(factory, "ru")
    locked, release, queued_done = threading.Event(), threading.Event(), threading.Event()
    original = api.lock_book_profile
    first = True
    def hold_first(db, book_id):
        nonlocal first
        book = original(db, book_id)
        if first:
            first = False
            locked.set()
            assert release.wait(5)
        return book
    monkeypatch.setattr(api, "lock_book_profile", hold_first)
    def queue():
        try:
            return client().post("/api/v2/books/book/run", json={"steps": ["segment"]})
        finally:
            queued_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        change = pool.submit(lambda: client().post("/api/v2/books/book/language", json={"language": "en"}))
        assert locked.wait(5)
        pending = pool.submit(queue)
        try:
            assert not queued_done.wait(0.1)
        finally:
            release.set()
        assert change.result(5).status_code == 200
        assert pending.result(5).status_code == 200
    with factory() as db:
        assert db.query(V2Run).one().language_snapshot == "en"


def test_snapshot_migration_is_idempotent_and_keeps_legacy_unknown(tmp_path):
    path = Path(__file__).parents[1] / "alembic/versions/0064_v2_run_language_snapshot.py"
    spec = importlib.util.spec_from_file_location("snapshot_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    metadata = MetaData()
    runs = Table("v2_runs", metadata, Column("id", String, primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(runs.insert().values(id="legacy"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert "language_snapshot" in {column["name"] for column in inspect(connection).get_columns("v2_runs")}
        assert connection.exec_driver_sql("SELECT language_snapshot FROM v2_runs").scalar() is None
    engine.dispose()


def test_ambient_snapshot_migration_is_idempotent_and_labels_legacy_ru():
    path = Path(__file__).parents[1] / "alembic/versions/0065_ambient_language_snapshot.py"
    spec = importlib.util.spec_from_file_location("ambient_snapshot_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    metadata = MetaData()
    tracks = Table("ambient_tracks", metadata, Column("id", String, primary_key=True))
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(tracks.insert().values(id="legacy"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
        assert connection.exec_driver_sql("SELECT language_snapshot FROM ambient_tracks").scalar() == "ru"
    engine.dispose()
