"""The run-control routes: progress for anyone with a session, run/stop/import for editors.

The queue is a seam: `enqueue_tracked_task` is replaced with a recorder, so the test
sees exactly what the worker would have been handed and nothing touches Redis.
"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main as app_main
from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import ScriptBook, ScriptChapter, ScriptLog
from app.v2.models import V2Run
from app.v2.segmenter import segment_chapter
from app.v2.store import store_chapter_segments

BOOK, CH1 = "book-run", "ch-run-1"
SOURCE = "Глава 1. Начало\n\nДгарнин сидел в изгибе ветвей.\n\n- Да.\n"


def _client(roles=None):
    client = TestClient(app)
    if roles is not None:
        client.cookies.set("session", session_serializer.dumps({"uid": "u1", "sub": "someone", "roles": roles}))
    return client


def _factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _patched(monkeypatch, *, enqueue_result="job-1"):
    SessionLocal = _factory()
    with SessionLocal() as db:
        db.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt", status="author_review"))
        db.add(ScriptChapter(id=CH1, book_id=BOOK, chapter_index=1, chapter_title="Глава 1", source_text=SOURCE))
        store_chapter_segments(db, book_id=BOOK, chapter_id=CH1, segments=segment_chapter(SOURCE, chapter_id=CH1))
        db.commit()
    monkeypatch.setattr("app.v2.api.SessionLocal", SessionLocal)
    calls = []

    def fake_enqueue(queue_name, func_ref, session_factory, background_run_cls, **kwargs):
        calls.append({"queue": queue_name, "func_ref": func_ref, **kwargs})
        return enqueue_result

    monkeypatch.setattr("app.v2.api.enqueue_tracked_task", fake_enqueue)
    return SessionLocal, calls


def test_routes_are_registered():
    paths = {getattr(route, "path", "") for route in app_main.app.routes}
    for suffix in ("progress", "run", "stop"):
        assert f"/api/v2/books/{{book_id}}/{suffix}" in paths
    # перенос разметки v1 удалён вместе с ней (2026-09-29)
    assert "/api/v2/books/{book_id}/import-legacy" not in paths


def test_all_three_need_a_session(monkeypatch):
    _patched(monkeypatch)
    client = _client()
    assert client.get(f"/api/v2/books/{BOOK}/progress").status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/run", json={}).status_code == 401
    assert client.post(f"/api/v2/books/{BOOK}/stop").status_code == 401


def test_dictors_read_progress_but_cannot_run_or_stop(monkeypatch):
    _, calls = _patched(monkeypatch)
    client = _client(["dictor"])
    assert client.get(f"/api/v2/books/{BOOK}/progress").status_code == 200
    assert client.get("/api/v2/books/nope/progress").status_code == 404
    assert client.post(f"/api/v2/books/{BOOK}/run", json={}).status_code == 403
    assert client.post(f"/api/v2/books/{BOOK}/stop").status_code == 403
    assert calls == []


def test_run_enqueues_on_high_marks_the_book_v2_and_refuses_a_second_run(monkeypatch):
    SessionLocal, calls = _patched(monkeypatch)
    client = _client(["author"])
    response = client.post(f"/api/v2/books/{BOOK}/run", json={"steps": ["stress", "attribute"], "force": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["job_id"] == "job-1" and body["steps"] == ["attribute", "stress"] and body["force"] is True

    assert len(calls) == 1
    call = calls[0]
    assert (call["queue"], call["func_ref"], call["job_kind"], call["run_key"]) == ("high", "app.worker_tasks.perform_v2_pipeline_task", "v2_pipeline", f"v2-pipeline:{BOOK}")
    assert (call["book_id"], call["steps"], call["force"], call["v2_run_id"]) == (BOOK, ["attribute", "stress"], True, body["run_id"])
    with SessionLocal() as db:
        book = db.get(ScriptBook, BOOK)
        assert (book.pipeline_mode, book.status, book.stop_requested) == ("v2", "processing", "false")
        run = db.get(V2Run, body["run_id"])
        assert run.status == "queued" and run.book_id == BOOK
        assert any("поставлен в очередь" in r.message for r in db.query(ScriptLog).filter(ScriptLog.book_id == BOOK).all())

    again = client.post(f"/api/v2/books/{BOOK}/run", json={})
    assert again.status_code == 409 and again.json()["error"] == "run_in_progress" and again.json()["run_id"] == body["run_id"]
    assert len(calls) == 1

    progress = client.get(f"/api/v2/books/{BOOK}/progress").json()
    assert progress["run"]["status"] == "queued" and progress["mode"] == "v2" and progress["status"] == "processing"


def test_run_with_no_body_takes_every_step_and_bad_steps_are_400(monkeypatch):
    _, calls = _patched(monkeypatch)
    client = _client(["admin"])
    response = client.post(f"/api/v2/books/{BOOK}/run")
    assert response.status_code == 200, response.text
    assert calls[0]["steps"] == ["segment", "cast", "attribute", "stress"] and calls[0]["force"] is False

    _, calls = _patched(monkeypatch)
    assert client.post(f"/api/v2/books/{BOOK}/run", json={"steps": ["polish"]}).status_code == 400
    assert client.post(f"/api/v2/books/{BOOK}/run", content=b"not json").status_code == 400
    assert client.post("/api/v2/books/nope/run", json={}).status_code == 404
    assert calls == []


def test_a_refused_enqueue_fails_the_queued_run(monkeypatch):
    SessionLocal, _ = _patched(monkeypatch, enqueue_result=None)
    response = _client(["author"]).post(f"/api/v2/books/{BOOK}/run", json={})
    assert response.status_code == 409 and response.json()["error"] == "enqueue_failed"
    with SessionLocal() as db:
        run = db.get(V2Run, response.json()["run_id"])
        assert run.status == "failed" and "enqueue failed" in run.error


def test_stop_raises_the_flag(monkeypatch):
    SessionLocal, _ = _patched(monkeypatch)
    client = _client(["author"])
    run_id = client.post(f"/api/v2/books/{BOOK}/run", json={}).json()["run_id"]
    response = client.post(f"/api/v2/books/{BOOK}/stop")
    assert response.status_code == 200 and response.json() == {"ok": True, "stop_requested": True, "run_id": run_id}
    with SessionLocal() as db:
        assert db.get(ScriptBook, BOOK).stop_requested == "true"
    assert client.get(f"/api/v2/books/{BOOK}/progress").json()["stop_requested"] is True
    assert client.post("/api/v2/books/nope/stop").status_code == 404
