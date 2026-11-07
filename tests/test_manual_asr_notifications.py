"""Ручная проверка отвечает инициатору, автоматическая сохраняет прежние письма."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth import session_serializer
from app.config import settings
from app.db import Base
from app.models import AsrJob, AudioFile, BackgroundRun, ScriptBook, ScriptChapter, TelegramAuthAccount, User
from app.services import asr_audit, asr_audit_lifecycle as lifecycle, asr_run, telegram
from app import worker_tasks as worker


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'manual-asr.db'}")
    Base.metadata.create_all(engine)
    result = sessionmaker(bind=engine, autoflush=False)
    with result() as db:
        db.add_all([
            User(id="admin-2", login="admin-2", password_hash="x"),
            TelegramAuthAccount(user_id="admin-2", telegram_user_id="202", role="admin"),
        ])
        db.commit()
    monkeypatch.setattr(worker, "SessionLocal", result)
    monkeypatch.setattr(lifecycle, "SessionLocal", result)
    monkeypatch.setattr("app.services.provider_keys.provider_key", lambda provider: "test-key")
    yield result
    engine.dispose()


@pytest.fixture
def sends(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "owner_telegram_id", "101")

    def send(db, text, **kwargs):
        sent.append((text, kwargs))
        return 1

    monkeypatch.setattr(telegram, "send_telegram_message", send)
    return sent


def _seed_asr(factory):
    with factory() as db:
        db.add(ScriptBook(id="book", title="Test book", source_filename="test.txt", source_format="txt"))
        db.add(ScriptChapter(id="chapter", book_id="book", chapter_index=1, chapter_title="Test chapter"))
        db.add(AudioFile(id="take", role_book_id="book", book_code="Test",
                         chapter="Test chapter", role="Narrator", actor_name="Test actor", kind="take",
                         original_filename="test.wav", stored_key="test.wav", mime_type="audio/wav", size_bytes=1))
        db.commit()


def _asr_services(monkeypatch, *, ready=True):
    letters = []

    def take(db, take_id, **kwargs):
        job = AsrJob(id="asr-job", audio_file_id=take_id, chapter_id="chapter", status="done")
        db.add(job)
        db.flush()
        return job

    monkeypatch.setattr(asr_run, "run_asr_for_take", take)
    monkeypatch.setattr(asr_run, "notify_take_missing", lambda db, job: letters.append(job.audio_file_id))
    monkeypatch.setattr("app.services.asr_borrow.borrow_across_roles", lambda *a: None)
    monkeypatch.setattr("app.services.chapter_delivery.mark_session_outdated", lambda *a: None)
    monkeypatch.setattr("app.services.audio_integrity.chapter_is_ready", lambda *a: ready)
    monkeypatch.setattr("app.services.chapter_coverage_report.build_chapter_report", lambda *a, **k: {})
    monkeypatch.setattr("app.services.chapter_coverage_report.render_chapter_report", lambda report: "Detailed chapter report")
    return letters


def test_manual_asr_sends_existing_detailed_report_to_initiator_without_changing_actor_letters(factory, sends, monkeypatch):
    _seed_asr(factory)
    letters = _asr_services(monkeypatch)
    fingerprint = asr_run.takes_fingerprint(["take"])
    with factory() as db:
        db.get(ScriptChapter, "chapter").coverage_reported_takes = fingerprint
        db.commit()
    worker.perform_asr_chapter_task("chapter", force=True, initiator_id="admin-2")
    assert sends == [("Detailed chapter report", {"chat_ids": ["202"], "direct": True})]
    assert letters == ["take"]
    with factory() as db:
        assert db.get(ScriptChapter, "chapter").coverage_reported_takes == fingerprint


def test_automatic_old_asr_task_keeps_owner_report_and_actor_letters(factory, sends, monkeypatch):
    _seed_asr(factory)
    letters = _asr_services(monkeypatch)
    worker.perform_asr_chapter_task("chapter", force=True)
    assert sends == [("Detailed chapter report", {})]
    assert letters == ["take"]
    with factory() as db:
        assert db.get(ScriptChapter, "chapter").coverage_reported_takes == asr_run.takes_fingerprint(["take"])


def test_manual_asr_incomplete_chapter_still_gets_summary(factory, sends, monkeypatch):
    _seed_asr(factory)
    _asr_services(monkeypatch, ready=False)
    worker.perform_asr_chapter_task("chapter", force=True, initiator_id="admin-2")
    assert len(sends) == 1
    text, targets = sends[0]
    assert "transcribed 1" in text
    assert targets == {"chat_ids": ["202"], "direct": True}
    with factory() as db:
        assert not db.get(ScriptChapter, "chapter").coverage_reported_takes


def test_manual_asr_missing_binding_never_falls_back_to_owner(factory, sends, monkeypatch):
    _seed_asr(factory)
    _asr_services(monkeypatch, ready=False)
    worker.perform_asr_chapter_task("chapter", force=True, initiator_id="missing-user")
    assert len(sends) == 1
    assert sends[0][1] == {"chat_ids": [], "direct": True}


def test_manual_asr_worker_failure_reports_safe_fact_to_initiator(factory, sends, monkeypatch):
    _seed_asr(factory)
    monkeypatch.setattr(asr_run, "run_asr_for_chapter", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("private api key")))
    monkeypatch.setattr(worker, "_fail_background_run", lambda *a: None)
    with pytest.raises(RuntimeError, match="private api key"):
        worker.perform_asr_chapter_task("chapter", initiator_id="admin-2")
    assert sends[0][1] == {"chat_ids": ["202"], "direct": True}
    assert "RuntimeError" in sends[0][0] and "private api key" not in sends[0][0]


def test_manual_routes_use_session_uid_and_auto_audit_has_no_initiator(factory, monkeypatch):
    from app.main import app
    from app.v2 import api

    _seed_asr(factory)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(api, "chapter_recording_status", lambda *a: {"roles": [{"files": [1]}]})
    monkeypatch.setattr(settings, "asr_audit_enabled", True)
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda *a: {"snapshot": "test"})
    calls = []
    monkeypatch.setattr(api, "enqueue_tracked_task", lambda **kwargs: calls.append(kwargs) or "asr-job")
    monkeypatch.setattr(asr_audit, "enqueue_asr_audit", lambda cid, **kwargs: calls.append(kwargs) or "audit-job")
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "admin-2", "sub": "admin-2", "roles": ["admin"]}))
    assert client.post("/api/v2/chapters/chapter/asr").status_code == 200
    assert calls[-1]["initiator_id"] == calls[-1]["meta"]["by"] == "admin-2"
    assert client.post("/api/v2/chapters/chapter/audit").status_code == 200
    assert calls[-1] == {"force": True, "initiator_id": "admin-2"}
    api._enqueue_assembled_audit("chapter")
    assert calls[-1] == {}


@pytest.mark.parametrize("initiator", ["admin-2", ""])
def test_audit_queue_persists_manual_initiator_without_changing_auto_kwargs(factory, monkeypatch, initiator):
    monkeypatch.setattr(settings, "asr_audit_enabled", True)
    snapshot = {"assembled_fingerprint": "assembly", "narrator_take_id": "take", "narrator_input_fingerprint": "input"}
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda *a: snapshot)
    enqueued = []
    monkeypatch.setattr(lifecycle, "get_queue", lambda name: SimpleNamespace(enqueue=lambda *a, **kw: enqueued.append(kw) or SimpleNamespace(id="job")))
    assert asr_audit.enqueue_asr_audit("chapter", force=True, initiator_id=initiator) == "job"
    with factory() as db:
        meta = json.loads(db.query(BackgroundRun).one().meta_json)
    if initiator:
        assert meta["by"] == enqueued[0]["initiator_id"] == initiator
    else:
        assert "by" not in meta and "initiator_id" not in enqueued[0]


@pytest.mark.parametrize("initiator", ["admin-2", ""])
def test_audit_report_splits_to_initiator_or_keeps_legacy_owner_path(factory, sends, initiator):
    personal = worker._initiator_notice(factory, initiator)
    with factory() as db:
        asr_audit._notify_owner(db,
            chapter=SimpleNamespace(chapter_title="Test", book_id="book", id="chapter"),
            book=SimpleNamespace(title="Test"), audit=SimpleNamespace(status="done", error_message="", stt_cost_rub=0, lines_json="{}"),
            lines={"required": []}, flags=[{"text": "word " * 1600, "missing_words": ["word"] * 900}], confirmed=[],
            **({"notify_result": personal} if personal is not None else {}))
    assert len(sends) > 1
    assert all(targets == ({"chat_ids": ["202"], "direct": True} if initiator else {}) for _, targets in sends)


def test_audit_changed_assembly_notifies_manual_initiator_once_without_payment(factory, sends, monkeypatch):
    _seed_asr(factory)
    with factory() as db:
        db.add(BackgroundRun(id="audit-run", job_kind="asr_audit", status="queued", meta_json="{}"))
        db.commit()
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda *a: None)
    monkeypatch.setattr(asr_audit, "run_asr_audit", lambda *a, **kw: pytest.fail("paid stale assembly"))
    worker.perform_asr_audit_task("chapter", run_id="audit-run", initiator_id="admin-2")
    worker.perform_asr_audit_task("chapter", run_id="audit-run", initiator_id="admin-2")
    assert len(sends) == 1 and sends[0][1] == {"chat_ids": ["202"], "direct": True}
    assert "no payment was made" in sends[0][0]
    with factory() as db:
        row = db.get(BackgroundRun, "audit-run")
        assert row.status == "skipped"
        assert json.loads(row.meta_json)["payment_state"] == "not_started"


@pytest.mark.parametrize("status", ["done", "failed"])
def test_audit_worker_routes_detailed_outcome_to_initiator_once(factory, sends, monkeypatch, status):
    _seed_asr(factory)
    with factory() as db:
        db.add(BackgroundRun(id="audit-run", job_kind="asr_audit", status="queued", meta_json="{}"))
        db.commit()
    snapshot = {"assembled_fingerprint": "assembly", "narrator_take_id": "take", "narrator_input_fingerprint": "input"}
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda *a: snapshot)
    calls = []

    def run(db, cid, **kwargs):
        calls.append(cid)
        kwargs["notify_result"]("Audit detailed result")
        return SimpleNamespace(status=status, error_message="")

    monkeypatch.setattr(asr_audit, "run_asr_audit", run)
    worker.perform_asr_audit_task("chapter", run_id="audit-run", initiator_id="admin-2", **snapshot)
    worker.perform_asr_audit_task("chapter", run_id="audit-run", initiator_id="admin-2", **snapshot)
    assert calls == ["chapter"]
    assert sends == [("Audit detailed result", {"chat_ids": ["202"], "direct": True})]


def test_actual_audit_changes_only_operator_report_and_keeps_narrator_letter(factory, monkeypatch, tmp_path):
    from tests.test_asr_audit import _make_ready_chapter, _heard_with, _judge, _spy_sends, NARRATION_FULL, NARRATION_PARTIAL

    root = tmp_path / "audio"
    (root / "k").mkdir(parents=True)
    (root / "k/take-r.wav").write_bytes(b"0" * 10)
    monkeypatch.setattr(settings, "audio_storage_path", str(root))
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a, **k: "/tmp/test-audit-window.mp3")
    personal = []
    with factory() as db:
        chapter_id = _make_ready_chapter(db)
        db.add(TelegramAuthAccount(telegram_user_id="111", role="dictor", display_name=db.get(AudioFile, "take-r").actor_name, is_active="true"))
        db.flush()
        letters = _spy_sends(monkeypatch)
        audit = asr_audit.run_asr_audit(db, chapter_id,
            transcribe=lambda path: (_heard_with(NARRATION_FULL, NARRATION_PARTIAL), 1.5),
            judge=_judge(confirm=True),
            confirm_transcribe=lambda path: ({"text": NARRATION_PARTIAL.replace("хотя ", "").replace("много ", ""), "segments": []}, 0.5),
            notify_result=personal.append)
        assert audit.status == "done"
    assert len(personal) == 1 and "confirmed 1" in personal[0]
    assert len(letters) == 1
    assert letters[0]["chat_ids"] == ["111"] and letters[0]["direct"] is True
    assert "Lines flagged by the strict word-for-word check" in letters[0]["text"]
