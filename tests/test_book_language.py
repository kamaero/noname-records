"""Смена языка сохраняет работу студии и явно помечает прежние ASR-результаты."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import AsrJob, AudioFile, BackgroundRun, ScriptBook, ScriptChapter
from app.services.chapter_delivery import book_recording_status, chapter_recording_status
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'language.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.api.SessionLocal", factory)
    with factory() as db:
        db.add(ScriptBook(id="book", title="Silver Keys", source_filename="sample.txt", source_format="txt", language="ru"))
        for index in range(6):
            chapter_id = f"chapter-{index}"
            db.add(ScriptChapter(id=chapter_id, book_id="book", chapter_index=index + 1,
                                 chapter_title=f"Chapter {index + 1}", status="published"))
            segment_id = f"segment-{index}"
            text = "I have twenty one silver keys."
            db.add(V2Segment(id=segment_id, book_id="book", chapter_id=chapter_id, ordinal=0, kind="paragraph", text=text))
            db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, version=1, speaker="Aster",
                                 span_start=0, span_end=len(text), source="operator"))
            db.add(AudioFile(id=f"audio-{index}", chapter=f"Chapter {index + 1}", book_code="SK", role="Aster",
                             kind="take", original_filename="keys.wav", stored_key=f"keys-{index}.wav", mime_type="audio/wav", size_bytes=10))
            db.add(AsrJob(id=f"job-{index}", chapter_id=chapter_id, audio_file_id=f"audio-{index}", language="ru",
                          status="done", heard_json='{"segments": []}', alignment_json=json.dumps({
                              "total": 1, "missing": [], "lines": [{"index": 0, "text": text, "matched": True}]})))
        db.commit()
    yield factory
    engine.dispose()


def _client(roles=None):
    client = TestClient(app)
    if roles is not None:
        client.cookies.set("session", session_serializer.dumps({"uid": "editor", "sub": "Test Editor", "roles": roles}))
    return client


@pytest.mark.parametrize("roles,expected", [(None, 401), ([], 403), (["dictor"], 403), (["agent"], 403)])
def test_book_language_change_requires_editor_access(factory, roles, expected):
    assert _client(roles).post("/api/v2/books/book/language", json={"language": "en"}).status_code == expected
    with factory() as db:
        assert db.get(ScriptBook, "book").language == "ru"


@pytest.mark.parametrize("payload", [{}, {"language": "fr"}, {"language": "en-US"}, {"language": ["en"]}, {"language": True}, []])
def test_book_language_rejects_invalid_json_values(factory, payload):
    assert _client(["admin"]).post("/api/v2/books/book/language", json=payload).status_code == 400


@pytest.mark.parametrize("roles", [["admin"], ["author"]])
def test_language_change_keeps_all_existing_work_and_does_not_enqueue_processing(factory, roles, monkeypatch):
    from app.workers import launcher
    monkeypatch.setattr(launcher, "enqueue_tracked_task", lambda *a, **kw: pytest.fail("automatic processing"))
    with factory() as db:
        before_jobs = [(row.id, row.language, row.heard_json, row.alignment_json) for row in db.query(AsrJob)]
        before_texts = [(row.id, row.text) for row in db.query(V2Segment)]
        before_edits = [(row.id, row.speaker, row.source) for row in db.query(V2Attribution)]
        before_audio = [(row.id, row.stored_key) for row in db.query(AudioFile)]
    response = _client(roles).post("/api/v2/books/book/language", json={"language": "en"})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "language": "en", "changed": True,
                               "review_required": True, "reprocessing_started": False}
    with factory() as db:
        assert db.get(ScriptBook, "book").language == "en"
        assert [(row.id, row.language, row.heard_json, row.alignment_json) for row in db.query(AsrJob)] == before_jobs
        assert [(row.id, row.text) for row in db.query(V2Segment)] == before_texts
        assert [(row.id, row.speaker, row.source) for row in db.query(V2Attribution)] == before_edits
        assert [(row.id, row.stored_key) for row in db.query(AudioFile)] == before_audio
        assert db.query(BackgroundRun).count() == 0


def test_coverage_exposes_stale_profile_without_changing_retained_coverage(factory):
    with factory() as db:
        assert book_recording_status(db, "book")["asr_language_stale"] is False
        db.get(ScriptBook, "book").language = "en"
        db.commit()
        status = book_recording_status(db, "book")
        assert status["asr_language_stale"] is True and status["language"] == "en"
        assert all(row["asr_language_stale"] and row["asr_stale_files"] == 1 and row["asr_coverage"] == 1
                   for row in status["chapters"])
        chapter = chapter_recording_status(db, "chapter-0")
        assert chapter["asr_language_stale"] and chapter["roles"][0]["asr_language_stale"]
        assert chapter["asr_coverage"] == 1
    payload = _client(["dictor"]).get("/api/v2/books/book/recording").json()
    assert payload["asr_language_stale"] is True


def test_stale_detection_uses_one_batched_asr_query_for_the_book(factory):
    statements = []
    engine = factory.kw["bind"]
    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement.lower())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with factory() as db:
            assert len(book_recording_status(db, "book")["chapters"]) == 6
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert sum("from asr_jobs" in query for query in statements) == 1
    assert sum("from script_books" in query for query in statements) == 1


def test_language_get_and_missing_book_are_guarded(factory):
    assert _client().get("/api/v2/books/book/language").status_code == 401
    assert _client(["dictor"]).get("/api/v2/books/book/language").json()["language"] == "ru"
    assert _client(["author"]).post("/api/v2/books/missing/language", json={"language": "en"}).status_code == 404
