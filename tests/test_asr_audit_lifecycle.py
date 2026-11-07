"""Сборка, резерв оплаты и повторная доставка без внешних сервисов."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace




def test_real_month_limit_blocks_paid_task(factory, queue):
    from app.models import SpendEntry
    from app.services.studio_settings import studio_settings
    with factory() as db:
        studio_settings(db).monthly_limit_rub = 1
        db.add(SpendEntry(step="asr", provider="routerai", model="test", rub=2.0, price_known=True))
        db.commit()
    with pytest.raises(lifecycle.AuditUnavailable, match="audit_over_limit"):
        lifecycle.enqueue_audit("chapter")
    assert queue == []

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import BackgroundRun
from app.services import asr_audit_lifecycle as lifecycle
from app.time_utils import utcnow_naive

SNAPSHOT = {"assembled_fingerprint": "assembled", "narrator_take_id": "take-r",
            "narrator_input_fingerprint": "narrator-input"}


def test_audit_queue_and_worker_preserve_language(factory, queue, monkeypatch):
    from app import worker_tasks
    from app.services import asr_audit
    snapshot = dict(SNAPSHOT, language="en")
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: snapshot)
    assert lifecycle.enqueue_audit("chapter")
    assert queue[0]["language"] == "en"
    with factory() as db:
        assert json.loads(db.query(BackgroundRun).one().meta_json)["language"] == "en"
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    seen = []
    monkeypatch.setattr(asr_audit, "run_asr_audit", lambda *a, **kw: seen.append(kw) or
                        SimpleNamespace(status="done", error_message=""))
    worker_tasks.perform_asr_audit_task(**queue[0])
    assert seen == [{"expected_narrator_fingerprint": "narrator-input", "language": "en"}]


def test_language_change_after_queue_prevents_audit_payment(factory, queue, monkeypatch):
    from app import worker_tasks
    from app.services import asr_audit
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: dict(SNAPSHOT, language="en"))
    assert lifecycle.enqueue_audit("chapter")
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: dict(SNAPSHOT, language="ru"))
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    monkeypatch.setattr(asr_audit, "run_asr_audit", lambda *a, **kw: pytest.fail("paid language mismatch"))
    worker_tasks.perform_asr_audit_task(**queue[0])
    with factory() as db:
        run = db.query(BackgroundRun).one()
        assert run.status == "skipped"
        assert json.loads(run.meta_json)["payment_state"] == "not_started"


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'audit.db'}", connect_args={"timeout": 15})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(lifecycle, "SessionLocal", factory)
    monkeypatch.setattr(lifecycle.settings, "asr_audit_enabled", True)
    from app.services import provider_keys
    monkeypatch.setattr(provider_keys, "provider_key", lambda provider: "test-key")
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: dict(SNAPSHOT))
    yield factory
    engine.dispose()


@pytest.fixture()
def queue(monkeypatch):
    calls = []

    def enqueue(*args, **kwargs):
        kwargs.pop("job_timeout", None)
        kwargs.pop("result_ttl", None)
        calls.append(kwargs)
        return SimpleNamespace(id="job-" + kwargs["run_id"])

    monkeypatch.setattr(lifecycle, "get_queue", lambda name: SimpleNamespace(enqueue=enqueue))
    return calls


def test_concurrent_assembly_reserves_one_paid_run(factory, queue):
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda _: lifecycle.enqueue_audit("chapter"), range(4)))
    assert sum(result is not None for result in results) == 1
    assert len(queue) == 1
    with factory() as db:
        assert db.query(BackgroundRun).count() == 1


def test_old_queued_or_running_job_never_expires_into_second_payment(factory, queue):
    assert lifecycle.enqueue_audit("chapter")
    with factory() as db:
        run = db.query(BackgroundRun).one()
        run.heartbeat_at = run.created_at = utcnow_naive() - timedelta(days=3)
        db.commit()
    assert lifecycle.enqueue_audit("chapter", force=True) is None
    with factory() as db:
        run = db.query(BackgroundRun).one()
        run.status = "running"
        db.commit()
    assert lifecycle.enqueue_audit("chapter", force=True) is None
    assert len(queue) == 1


def test_unknown_paid_outcome_requires_manual_repeat(factory, queue):
    assert lifecycle.enqueue_audit("chapter")
    with factory() as db:
        run = db.query(BackgroundRun).one()
        run.status = "failed"
        db.commit()
    assert lifecycle.enqueue_audit("chapter") is None
    assert lifecycle.enqueue_audit("chapter", force=True)
    assert len(queue) == 2


def test_other_role_changes_do_not_pay_but_narrator_fix_does(factory, queue, monkeypatch):
    assert lifecycle.enqueue_audit("chapter")
    with factory() as db:
        db.query(BackgroundRun).one().status = "done"
        db.commit()
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: dict(SNAPSHOT, assembled_fingerprint="other-role"))
    assert lifecycle.enqueue_audit("chapter") is None
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: dict(SNAPSHOT, narrator_input_fingerprint="fixed"))
    assert lifecycle.enqueue_audit("chapter")
    assert len(queue) == 2


def test_queue_ambiguous_failure_keeps_reservation(factory, monkeypatch):
    def fail(*args, **kwargs):
        raise TimeoutError("Redis response lost")

    monkeypatch.setattr(lifecycle, "get_queue", lambda name: SimpleNamespace(enqueue=fail))
    assert lifecycle.enqueue_audit("chapter") is None
    assert lifecycle.enqueue_audit("chapter", force=True) is None
    with factory() as db:
        run = db.query(BackgroundRun).one()
        assert run.status == "queued"
        assert "queue_outcome_unknown" in run.error_message


def test_worker_claim_blocks_duplicate_delivery_and_legacy_untracked_job(factory, queue, monkeypatch):
    import app.worker_tasks as worker
    from app.services import asr_audit

    assert lifecycle.enqueue_audit("chapter")
    kwargs = queue[0]
    monkeypatch.setattr(worker, "SessionLocal", factory)
    calls = []
    monkeypatch.setattr(asr_audit, "run_asr_audit", lambda db, cid, **kwargs: calls.append((cid, kwargs)) or SimpleNamespace(status="done", error_message=""))
    worker.perform_asr_audit_task(**kwargs)
    worker.perform_asr_audit_task(**kwargs)
    worker.perform_asr_audit_task("chapter")
    assert calls == [("chapter", {"expected_narrator_fingerprint": "narrator-input"})]


def test_worker_stale_assembly_stops_before_paid_service(factory, queue, monkeypatch):
    import app.worker_tasks as worker
    from app.services import asr_audit

    assert lifecycle.enqueue_audit("chapter")
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: None)
    monkeypatch.setattr(asr_audit, "run_asr_audit", lambda *a, **k: pytest.fail("paid stale chapter"))
    worker.perform_asr_audit_task(**queue[0])
    with factory() as db:
        assert db.query(BackgroundRun).one().status == "skipped"


def test_manual_route_is_admin_only_and_requires_current_assembly(monkeypatch):
    from fastapi.testclient import TestClient
    from app.auth import session_serializer
    from app.main import app
    import app.v2.api as api
    from app.services import asr_audit

    calls = []
    monkeypatch.setattr(asr_audit, "enqueue_asr_audit", lambda cid, **kw: calls.append((cid, kw)) or "job")
    monkeypatch.setattr(lifecycle.settings, "asr_audit_enabled", True)

    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, *args):
            return SimpleNamespace(id="chapter")

    monkeypatch.setattr(api, "SessionLocal", Session)
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: dict(SNAPSHOT))
    assert TestClient(app).post("/api/v2/chapters/chapter/audit").status_code == 401
    for role in ("author", "agent", "dictor"):
        client = TestClient(app)
        client.cookies.set("session", session_serializer.dumps({"uid": "u", "sub": "u", "roles": [role]}))
        assert client.post("/api/v2/chapters/chapter/audit").status_code == 403
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "u", "sub": "u", "roles": ["admin"]}))
    assert client.post("/api/v2/chapters/chapter/audit").status_code == 200
    assert calls == [("chapter", {"force": True})]
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: None)
    response = client.post("/api/v2/chapters/chapter/audit")
    assert response.status_code == 409
    assert response.json()["error"] == "chapter_not_assembled"
    assert len(calls) == 1


def test_nas_route_commits_assembly_before_enqueue(monkeypatch):
    from tests.test_chapter_archive_routes import _client
    import app.v2.api as api
    monkeypatch.setattr(lifecycle.settings, "asr_audit_enabled", True)

    events = []

    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def commit(self):
            events.append("commit")

    monkeypatch.setattr(api, "SessionLocal", Session)
    monkeypatch.setattr(api, "archive_chapter_session", lambda db, cid: {"written": True, "reason": "", "replaced": ""})
    monkeypatch.setattr(lifecycle, "record_audit_assembly", lambda db, cid: events.append("assembly") or True)
    monkeypatch.setattr(api, "_enqueue_assembled_audit", lambda cid: events.append("enqueue"))
    assert _client(["admin"]).post("/api/v2/chapters/chapter/session-archive").status_code == 200
    assert events == ["assembly", "commit", "enqueue"]


@pytest.mark.parametrize(("ready", "session", "missing", "triggers"), [
    (True, True, [], True), (False, True, [], False),
    (True, False, [], False), (True, True, ["lost.wav"], False),
])
def test_zip_requires_actual_complete_project_before_recording(monkeypatch, ready, session, missing, triggers):
    import zipfile
    from tests.test_chapter_archive_routes import _client
    import app.v2.api as api
    monkeypatch.setattr(lifecycle.settings, "asr_audit_enabled", True)

    events = []

    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def commit(self):
            events.append("commit")

    def build(db, cid, target):
        with zipfile.ZipFile(target, "w") as archive:
            if session:
                archive.writestr("сессия.sesx", "project")
        return {"ready": ready, "files_missing": missing}

    monkeypatch.setattr(api, "SessionLocal", Session)
    monkeypatch.setattr(api, "chapter_has_broken_files", lambda db, cid: False)
    monkeypatch.setattr(api, "build_chapter_archive", build)
    monkeypatch.setattr(lifecycle, "record_audit_assembly", lambda db, cid: events.append("assembly") or True)
    monkeypatch.setattr(api, "_enqueue_assembled_audit", lambda cid: events.append("enqueue"))
    assert _client(["admin"]).get("/api/v2/chapters/chapter/archive.zip").status_code == 200
    assert events == (["assembly", "commit", "enqueue"] if triggers else ["commit"])


def test_snapshot_requires_full_coverage_and_preserves_assembly_proof(factory, monkeypatch):
    from app.models import ScriptBook, ScriptChapter
    from app.services import asr_audit, chapter_delivery, audio_integrity

    with factory() as db:
        db.add(ScriptBook(id="book", title="Книга", source_filename="book.txt", source_format="txt"))
        db.add(ScriptChapter(id="chapter", book_id="book", chapter_index=1, chapter_title="Глава 1"))
        db.commit()
    row = {"chapter_id": "chapter", "ready": True, "total_lines": 2, "matched_lines": 1,
           "missing_lines": 1, "asr_files": 1}
    monkeypatch.setattr(audio_integrity, "chapter_has_broken_files", lambda db, cid: False)
    monkeypatch.setattr(lifecycle, "_assembly_sources_available", lambda db, cid: row["matched_lines"] == row["total_lines"])
    monkeypatch.setattr(asr_audit, "latest_narrator_take", lambda db, chapter: SimpleNamespace(id="take"))
    monkeypatch.setattr(asr_audit, "narrator_input_fingerprint", lambda db, cid: "narrator", raising=False)
    monkeypatch.setattr(asr_audit, "narrator_lines", lambda db, cid: {"required": [{"text": "Нужная строка"}]})
    monkeypatch.setattr(chapter_delivery, "chapter_recording_status", lambda db, cid: row)
    builds = []
    monkeypatch.setattr(chapter_delivery, "build_chapter_session", lambda db, cid, **kw: builds.append(kw) or "xml")
    with factory() as db:
        assert lifecycle.audit_assembly_snapshot(db, "chapter") is None
        assert builds == []
        row.update(matched_lines=2, missing_lines=0)
        assert lifecycle.record_audit_assembly(db, "chapter")
        assert builds == [{"relative": True, "include_audit": False}]
        db.commit()
        # Наличие всех файлов ещё не отменяет правило «сборку надо сохранить».
        chapter = db.get(ScriptChapter, "chapter")
        assert chapter.audit_narrator_fingerprint == "narrator"


def test_concurrent_worker_claim_has_one_winner(factory, queue):
    assert lifecycle.enqueue_audit("chapter")
    run_id = queue[0]["run_id"]

    def claim(_):
        with factory() as db:
            return lifecycle.claim_audit_run(db, run_id)

    with ThreadPoolExecutor(max_workers=4) as workers:
        assert sum(workers.map(claim, range(4))) == 1


@pytest.fixture()
def complete_real_project(tmp_path, monkeypatch):
    from app.models import AsrJob
    from tests.test_asr_audit import _make_ready_chapter

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(lifecycle.settings, "audio_storage_path", str(tmp_path / "local"))
    monkeypatch.setattr(lifecycle.settings, "audio_nas_path", str(tmp_path / "nas"))
    with sessionmaker(bind=engine)() as db:
        chapter_id = _make_ready_chapter(db)
        job = db.query(AsrJob).one()
        payload = json.loads(job.alignment_json)
        for index, line in enumerate(payload["lines"]):
            line.update(matched=True, start=index * 6., end=index * 6. + 5.)
        payload.update(matched=len(payload["lines"]), missing=[])
        job.alignment_json = json.dumps(payload)
        db.commit()
        yield db, chapter_id
    engine.dispose()


def test_snapshot_rejects_missing_physical_source_despite_complete_db_coverage(complete_real_project):
    db, chapter_id = complete_real_project
    assert lifecycle.audit_assembly_snapshot(db, chapter_id) is None
    assert lifecycle.record_audit_assembly(db, chapter_id) is False


def test_snapshot_rejects_offline_nas_without_stat_or_open(complete_real_project, monkeypatch):
    from app.models import AudioFile
    from app.services import nas_health

    db, chapter_id = complete_real_project
    db.get(AudioFile, "take-r").location = "nas"
    db.commit()
    monkeypatch.setattr(nas_health, "nas_online", lambda: False)
    monkeypatch.setattr(lifecycle.os.path, "isfile", lambda path: pytest.fail("offline NAS stat"))
    assert lifecycle.audit_assembly_snapshot(db, chapter_id) is None
    assert lifecycle.record_audit_assembly(db, chapter_id) is False


def test_snapshot_checks_other_role_used_by_complete_project(complete_real_project, monkeypatch, tmp_path):
    from pathlib import Path
    from app.models import AudioFile, AsrJob
    from app.v2.models import V2Attribution, V2Segment
    from uuid import uuid4

    db, chapter_id = complete_real_project
    narrator_path = Path(lifecycle.settings.audio_storage_path) / "k/take-r.wav"
    narrator_path.parent.mkdir(parents=True)
    narrator_path.write_bytes(b"narrator source")
    segment = V2Segment(id="other-role-line", book_id="book-asr-audit", chapter_id=chapter_id,
                        ordinal=50, kind="paragraph", text="Реплика героя.")
    db.add(segment)
    db.add(V2Attribution(id=str(uuid4()), segment_id=segment.id, version=1, speaker="Герой",
                         span_start=0, span_end=len(segment.text), source="operator"))
    db.add(AudioFile(id="hero",
        book_code="КП", chapter="Глава 12. Дорога", role="Герой", kind="take", stored_key="hero.wav",
        original_filename="hero.wav", canonical_filename="hero.wav", mime_type="audio/wav", size_bytes=10))
    db.add(AsrJob(audio_file_id="hero", chapter_id=chapter_id, status="done", alignment_json=json.dumps({
        "total": 1, "matched": 1, "missing": [], "lines": [
            {"index": 0, "matched": True, "text": segment.text, "start": 0., "end": 2.}]})))
    db.commit()
    assert lifecycle.audit_assembly_snapshot(db, chapter_id) is None
    (Path(lifecycle.settings.audio_storage_path) / "hero.wav").write_bytes(b"hero source")
    assert lifecycle.audit_assembly_snapshot(db, chapter_id) is not None


def test_foreign_book_with_same_title_cannot_complete_project(complete_real_project):
    from pathlib import Path
    from app.models import AudioFile, AsrJob
    from app.services.chapter_delivery import book_recording_status, chapter_recording_status
    from tests.test_asr_audit import TITLE_LINE, BOOK

    db, chapter_id = complete_real_project
    source = Path(lifecycle.settings.audio_storage_path) / "k/take-r.wav"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"narrator source")
    original = db.query(AsrJob).one()
    original_payload = json.loads(original.alignment_json)
    original_payload["lines"][0]["matched"] = False
    original_payload.update(matched=3, missing=[0])
    original.alignment_json = json.dumps(original_payload)
    db.add(AudioFile(id="foreign", book_code="ДК",
        chapter=TITLE_LINE, role="Рассказчик", kind="take", stored_key="foreign.wav",
        original_filename="foreign.wav", canonical_filename="foreign.wav", mime_type="audio/wav", size_bytes=10))
    complete = json.loads(original.alignment_json)
    complete["lines"][0]["matched"] = True
    complete.update(matched=4, missing=[])
    db.add(AsrJob(audio_file_id="foreign", chapter_id="other-chapter", status="done", alignment_json=json.dumps(complete)))
    db.commit()
    # Чужой дубль исключён и из сводки, и из платного основания сборки.
    assert book_recording_status(db, BOOK)["chapters"][0]["matched_lines"] == 3
    assert chapter_recording_status(db, chapter_id)["matched_lines"] == 3
    assert lifecycle.audit_assembly_snapshot(db, chapter_id) is None


def test_original_plus_fix_can_complete_project_despite_partial_aggregate(complete_real_project):
    from pathlib import Path
    from app.models import AsrJob
    from app.services.chapter_delivery import chapter_recording_status
    from tests.test_asr_audit import _add_fix

    db, chapter_id = complete_real_project
    root = Path(lifecycle.settings.audio_storage_path)
    (root / "k").mkdir(parents=True)
    (root / "k/take-r.wav").write_bytes(b"original source")
    (root / "k/fix.wav").write_bytes(b"fixed source")
    original = db.query(AsrJob).one()
    payload = json.loads(original.alignment_json)
    for index in (1, 2, 3):
        payload["lines"][index]["matched"] = False
    payload.update(matched=1, missing=[1, 2, 3])
    original.alignment_json = json.dumps(payload)
    _add_fix(db)
    db.commit()
    # Первый обязательный кусок пришёл из оригинала, второй — из фикса;
    # заголовок и короткую авторскую ремарку не требуем от рассказчика.
    status = chapter_recording_status(db, chapter_id)
    assert status["matched_lines"] < status["total_lines"]
    assert lifecycle.audit_assembly_snapshot(db, chapter_id) is not None


def test_worker_without_web_nas_flag_uses_bounded_probe(complete_real_project, monkeypatch):
    from pathlib import Path
    from app.models import AudioFile
    from app.services import nas_health

    db, chapter_id = complete_real_project
    db.get(AudioFile, "take-r").location = "nas"
    db.commit()
    root = Path(lifecycle.settings.audio_nas_path)
    (root / "k").mkdir(parents=True)
    (root / "k/take-r.wav").write_bytes(b"NAS narrator source")
    calls = []
    monkeypatch.setattr(nas_health, "nas_online", lambda: None)
    monkeypatch.setattr(nas_health, "reachable_now", lambda timeout: calls.append(timeout) or True)
    assert lifecycle.audit_assembly_snapshot(db, chapter_id) is not None
    assert calls == [5.0]


def test_postpaid_skipped_result_never_repeats_automatically_after_input_revert(factory, queue):
    from app.models import AsrAudit

    assert lifecycle.enqueue_audit("chapter")
    with factory() as db:
        run = db.query(BackgroundRun).one()
        run.status = "skipped"
        run.error_message = "аудит skipped: narrator_input_changed_after_payment"
        db.add(AsrAudit(chapter_id="chapter", status="skipped", stt_cost_rub=2.,
            error_message="narrator_input_changed_after_payment", lines_json=json.dumps({
                "narrator_input_fingerprint": SNAPSHOT["narrator_input_fingerprint"], "stt_calls": 2})))
        db.commit()
    assert lifecycle.enqueue_audit("chapter") is None
    assert len(queue) == 1
    assert lifecycle.enqueue_audit("chapter", force=True)


def test_ambiguous_skipped_without_explicit_prepayment_reason_blocks_repeat(factory, queue):
    assert lifecycle.enqueue_audit("chapter")
    with factory() as db:
        run = db.query(BackgroundRun).one()
        run.status = "skipped"
        run.error_message = "unknown outcome"
        db.commit()
    assert lifecycle.enqueue_audit("chapter") is None
    assert len(queue) == 1


def test_worker_prepayment_skip_can_be_automatically_queued_again(factory, queue, monkeypatch):
    import app.worker_tasks as worker

    assert lifecycle.enqueue_audit("chapter")
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: None)
    worker.perform_asr_audit_task(**queue[0])
    with factory() as db:
        run = db.query(BackgroundRun).one()
        meta = json.loads(run.meta_json)
        assert meta["payment_state"] == "not_started"
        assert meta["skip_reason"] == "assembly_changed_before_payment"
    monkeypatch.setattr(lifecycle, "assembled_audit_snapshot", lambda db, cid: dict(SNAPSHOT))
    assert lifecycle.enqueue_audit("chapter")
    assert len(queue) == 2


def test_legacy_empty_audit_meta_does_not_crash_reservation(factory, queue):
    from app.models import AsrAudit

    with factory() as db:
        db.add(AsrAudit(chapter_id="chapter", status="skipped", error_message="narrator_input_changed", lines_json=""))
        db.commit()
    assert lifecycle.enqueue_audit("chapter")


def test_standalone_ambiguous_audit_for_same_input_requires_explicit_repeat(factory, queue):
    from app.models import AsrAudit

    with factory() as db:
        db.add(AsrAudit(chapter_id="chapter", status="skipped", error_message="unknown_outcome",
            lines_json=json.dumps({"narrator_input_fingerprint": SNAPSHOT["narrator_input_fingerprint"]})))
        db.commit()
    assert lifecycle.enqueue_audit("chapter") is None
    assert queue == []


def test_optional_only_project_can_archive_without_reserving_paid_audit(complete_real_project, monkeypatch, queue):
    from pathlib import Path
    from app.models import AsrJob
    from app.v2.models import V2Attribution, V2Segment
    from app.services import chapter_delivery, nas_health, asr_audit
    from tests.test_asr_audit import TAG_LINE, TITLE_LINE

    db, chapter_id = complete_real_project
    ids = [f"{chapter_id}:00000", f"{chapter_id}:00001"]
    db.query(V2Attribution).filter(V2Attribution.segment_id.in_(ids)).delete(synchronize_session=False)
    db.query(V2Segment).filter(V2Segment.id.in_(ids)).delete(synchronize_session=False)
    job = db.query(AsrJob).one()
    job.alignment_json = json.dumps({"total": 2, "matched": 2, "missing": [], "lines": [
        {"index": 0, "matched": True, "text": TAG_LINE, "start": 0., "end": 1.},
        {"index": 1, "matched": True, "text": TITLE_LINE, "start": 2., "end": 4.},
    ]})
    db.commit()
    local = Path(lifecycle.settings.audio_storage_path)
    (local / "k").mkdir(parents=True)
    (local / "k/take-r.wav").write_bytes(b"optional source")
    nas = Path(lifecycle.settings.audio_nas_path)
    nas.mkdir(parents=True)
    monkeypatch.setattr(nas_health, "nas_online", lambda: True)
    monkeypatch.setattr(lifecycle.settings, "asr_audit_enabled", True)
    monkeypatch.setattr(lifecycle, "SessionLocal", sessionmaker(bind=db.bind))

    assert asr_audit.narrator_lines(db, chapter_id)["required"] == []
    assert lifecycle._assembly_addresses_complete(db, chapter_id)
    result = chapter_delivery.archive_chapter_session(db, chapter_id)
    db.commit()
    assert result["written"] is True
    assert len(list(nas.rglob("*.sesx"))) == 1
    assert lifecycle.audit_assembly_snapshot(db, chapter_id) is None
    assert lifecycle.record_audit_assembly(db, chapter_id) is False
    assert lifecycle.enqueue_audit(chapter_id) is None
    assert queue == []
    assert db.query(BackgroundRun).count() == 0



def test_single_seat_queue_runs_the_audit_once(factory, monkeypatch):
    from app.services import asr_audit
    from app.workers.local_queue import LocalQueue
    import app.worker_tasks as tasks
    line = LocalQueue("audit-test")
    calls = []
    monkeypatch.setattr(lifecycle, "get_queue", lambda name: line)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(asr_audit, "run_asr_audit", lambda db, chapter_id, **kw:
                        calls.append(chapter_id) or SimpleNamespace(status="done", error_message=""))
    assert lifecycle.enqueue_audit("chapter")
    assert line.join(timeout=10)
    assert calls == ["chapter"]
    with factory() as db:
        assert db.query(BackgroundRun).one().status == "done"


def test_missing_router_key_never_reserves_or_queues(factory, queue, monkeypatch):
    from app.services import provider_keys
    monkeypatch.setattr(provider_keys, "provider_key", lambda provider: "")
    with pytest.raises(lifecycle.AuditUnavailable, match="audit_missing_key"):
        lifecycle.enqueue_audit("chapter")
    with factory() as db:
        assert db.query(BackgroundRun).count() == 0
    assert queue == []


def test_exhausted_budget_never_starts_paid_task(factory, queue, monkeypatch):
    from app.services import spend
    monkeypatch.setattr(spend, "check_start", lambda *a, **kw: SimpleNamespace(allowed=False))
    with pytest.raises(lifecycle.AuditUnavailable, match="audit_over_limit"):
        lifecycle.enqueue_audit("chapter")
    assert queue == []
    with factory() as db:
        run = db.query(BackgroundRun).one()
        assert run.status == "skipped"
        assert json.loads(run.meta_json)["payment_state"] == "not_started"
