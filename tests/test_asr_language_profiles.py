"""Профиль книги проходит через настоящую сверку, очередь и бесплатный повтор."""
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AsrJob, ScriptBook
from app.services import asr_run
from tests.asr_studio import add_take, heard, make_chapter, move_line


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def _english_chapter(db):
    chapter = make_chapter(db, [("Aster", '“I have 21 keys,” said Aster.')], {"Aster": "Test Actor"})
    db.get(ScriptBook, "b1").language = "en"
    db.commit()
    return chapter


def test_english_expected_speech_excludes_author_tag(db):
    chapter = _english_chapter(db)
    assert asr_run.expected_lines(db, chapter, "Aster") == ["I have 21 keys,"]


def test_take_saves_language_before_network_and_survives_book_change(db, monkeypatch):
    chapter = _english_chapter(db)
    from app.services import spend, step_models
    monkeypatch.setattr(step_models, "step_model", lambda step: ("openai", "test"))
    monkeypatch.setattr(spend, "record_call", lambda *a, **kw: None)
    # Создаём файл через штатную тестовую фабрику, затем повторяем платный путь заглушкой.
    job = add_take(db, chapter, role="Aster", actor="Test Actor", said=["I have twenty one keys"])
    seen = []
    def transcribe(path, **kw):
        seen.append((kw["language"], db.query(AsrJob).one().language))
        db.get(ScriptBook, "b1").language = "ru"
        db.commit()
        return heard("I have twenty one keys")(path)
    monkeypatch.setattr(asr_run, "_transcribe_with_service", transcribe)
    result = asr_run.run_asr_for_take(db, job.audio_file_id, notify=False)
    assert seen == [("en", "en")]
    assert result.language == "en" and result.coverage == 1.0
    assert json.loads(result.alignment_json)["language"] == "en"


def test_realign_keeps_paid_profile_and_never_calls_transcription_after_language_change(db, monkeypatch):
    from app.services.asr_replay import realign_job
    chapter = _english_chapter(db)
    job = add_take(db, chapter, role="Aster", actor="Test Actor", said=["I have twenty one keys"])
    before = job.heard_json
    db.get(ScriptBook, "b1").language = "ru"
    db.commit()
    monkeypatch.setattr(asr_run, "_transcribe_with_service", lambda *a, **kw: pytest.fail("paid replay"))
    assert realign_job(db, job.id) is not None
    result = asr_run.run_asr_for_chapter(db, chapter)
    assert result["done"] == 0 and result["realigned"] == 1
    assert job.language == "en" and job.coverage == 1.0 and job.heard_json == before


def test_failed_paid_repeat_preserves_original_transcript_language_for_free_replay(db, monkeypatch):
    from app.services.asr_replay import realign_job
    chapter = _english_chapter(db)
    job = add_take(db, chapter, role="Aster", actor="Test Actor", said=["I have twenty one keys"])
    original = job.heard_json
    db.get(ScriptBook, "b1").language = "ru"
    db.commit()
    def unavailable(path):
        raise RuntimeError("timeout")
    assert asr_run.run_asr_for_take(db, job.audio_file_id, transcribe=unavailable, notify=False).status == "failed"
    assert job.heard_json == original
    monkeypatch.setattr(asr_run, "_transcribe_with_service", lambda *a, **kw: pytest.fail("paid old cached take"))
    assert asr_run.run_asr_for_chapter(db, chapter)["realigned"] == 1
    assert job.language == "en" and job.coverage == 1.0


def test_borrow_uses_english_numbers_after_role_reassignment(db):
    from app.services.asr_borrow import borrow_across_roles
    from app.services.asr_replay import realign_job
    chapter = make_chapter(db, [("Aster", "I have 21 silver keys"),
                               ("Birch", "The boat has left the harbor")],
                           {"Aster": "Test Actor", "Birch": "Test Actor"})
    db.get(ScriptBook, "b1").language = "en"
    db.commit()
    first = add_take(db, chapter, role="Aster", actor="Test Actor", said=["I have twenty one silver keys"])
    second = add_take(db, chapter, role="Birch", actor="Test Actor", said=["The boat has left the harbor"])
    move_line(db, chapter, 0, "Birch")
    for job in (first, second):
        realign_job(db, job.id)
    assert borrow_across_roles(db, chapter)["borrowed"] == 1
    line = json.loads(second.alignment_json)["lines"][0]
    assert line["matched"] and line["source_audio_file_id"] == first.audio_file_id


def test_borrow_does_not_mix_paid_profiles(db):
    from app.services.asr_borrow import borrow_across_roles
    from app.services.asr_replay import realign_job
    chapter = make_chapter(db, [("Aster", "I have silver keys"),
                               ("Birch", "The boat has left the harbor")],
                           {"Aster": "Test Actor", "Birch": "Test Actor"})
    first = add_take(db, chapter, role="Aster", actor="Test Actor", said=["I have silver keys"])
    db.get(ScriptBook, "b1").language = "en"
    db.commit()
    second = add_take(db, chapter, role="Birch", actor="Test Actor", said=["The boat has left the harbor"])
    move_line(db, chapter, 0, "Birch")
    for job in (first, second):
        realign_job(db, job.id)
    assert (first.language, second.language) == ("ru", "en")
    assert borrow_across_roles(db, chapter)["borrowed"] == 0


@pytest.mark.parametrize("provider,expected", [("openai", "en"), ("azure", "en-US")])
def test_provider_receives_book_language_instead_of_global_russian(db, monkeypatch, tmp_path, provider, expected):
    from app.services import provider_keys
    seen = []
    monkeypatch.setattr(provider_keys, "provider_key", lambda p: "test")
    monkeypatch.setattr(asr_run, "compress_for_asr", lambda *a: None)
    monkeypatch.setattr(asr_run, "transcribe_file", lambda path, **kw: seen.append(kw["language"]) or {})
    monkeypatch.setattr(asr_run, "transcribe_file_azure", lambda path, **kw: seen.append(kw["locale"]) or {})
    asr_run._transcribe_with_service(str(tmp_path / "take.wav"), provider=provider, model="test", language="en")
    assert seen == [expected]


def test_automatic_queue_captures_language_before_worker_start(db, monkeypatch):
    from app import db as db_module
    from app.workers import launcher
    chapter = _english_chapter(db)
    monkeypatch.setattr(db_module, "SessionLocal", lambda: db)
    seen = []
    monkeypatch.setattr(launcher, "enqueue_tracked_task", lambda **kw: seen.append(kw) or "job")
    assert asr_run.enqueue_asr_for_chapter(chapter) == "job"
    assert seen[0]["language"] == seen[0]["meta"]["language"] == "en"


def test_manual_paid_queue_captures_language_before_worker_start(db, monkeypatch):
    from app.v2 import api
    chapter = _english_chapter(db)
    monkeypatch.setattr(api, "SessionLocal", lambda: db)
    monkeypatch.setattr(api, "_is_authenticated", lambda request: True)
    monkeypatch.setattr(api, "_can_edit", lambda request: True)
    monkeypatch.setattr(api, "chapter_recording_status", lambda *a: {"roles": [{"files": ["take"]}]})
    seen = []
    monkeypatch.setattr(api, "enqueue_tracked_task", lambda **kw: seen.append(kw) or "job")
    assert api.api_v2_chapter_asr(SimpleNamespace(), chapter).status_code == 200
    assert seen[0]["language"] == seen[0]["meta"]["language"] == "en"
    assert seen[0]["force"] is True


def test_worker_uses_queued_language_instead_of_current_book(monkeypatch):
    from app import worker_tasks
    class Session:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def commit(self): pass
    monkeypatch.setattr(worker_tasks, "SessionLocal", Session)
    monkeypatch.setattr(worker_tasks, "_start_background_run", lambda *a: None)
    monkeypatch.setattr(worker_tasks, "_finish_background_run", lambda *a: None)
    seen = []
    monkeypatch.setattr(asr_run, "run_asr_for_chapter", lambda *a, **kw: seen.append(kw) or {"done": 1, "failed": 0})
    worker_tasks.perform_asr_chapter_task("chapter", "run", language="en")
    assert seen == [{"force": False, "language": "en"}]
