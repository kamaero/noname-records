"""Сверка главы в воркере не должна виснуть на мёртвом NAS — и не должна отменяться
из-за него, если сверять нечего, кроме локального диска.

Флаг сторожа живёт в памяти процесса, а пробу крутит только веб-процесс: воркер про
состояние монтирования не знает ничего. На `hard`-монтировании первое же чтение файла
не падает, а виснет — и задание занимает единственный воркер очереди `high` до
возвращения NAS, вместе со всей очередью распознавания за ним.

Но `location='nas'` — это только наследие до переезда приёма на локальный диск
сервера: у новых дублей его не бывает. Поэтому проба NAS больше не гейтит всю
задачу — она нужна, только если в главе вообще есть строки на зеркале, и её
недоступность метит `skipped` только их, а не проваливает сверку локальных
файлов главы.
"""
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.worker_tasks as worker_tasks
from app.models import Base, BackgroundRun
from app.time_utils import utcnow_naive


class _Take:
    """Минимальная замена AudioFile: воркеру нужно только поле `location`."""

    def __init__(self, location: str):
        self.location = location


@pytest.fixture()
def run_row(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(worker_tasks, "SessionLocal", sessions)
    with sessions() as db:
        run = BackgroundRun(
            run_key="verify-chapter-ch-1", job_kind="verify_chapter", entity_type="script_chapter",
            entity_id="ch-1", status="queued", thread_name="rq:high", heartbeat_at=utcnow_naive(),
        )
        db.add(run)
        db.commit()
        run_id = str(run.id)
    return sessions, run_id


def _statuses(sessions, run_id):
    with sessions() as db:
        run = db.query(BackgroundRun).filter(BackgroundRun.id == run_id).first()
        return str(run.status), str(run.error_message or "")


def test_a_chapter_of_only_local_files_never_probes_the_nas(run_row, monkeypatch):
    """Глава без единой строки на зеркале не имеет причины спрашивать монтирование:
    проба стоила бы времени (до `NAS_PROBE_TIMEOUT_SECONDS`) ради вопроса, ответ на
    который сверке не нужен."""
    sessions, run_id = run_row
    monkeypatch.setattr("app.services.audio_integrity.chapter_files",
                        lambda db, chapter_id: [_Take("local"), _Take("local")])
    probed: list[str] = []
    monkeypatch.setattr("app.services.nas_health.probe_once", lambda root: probed.append(root) or True)
    verified: list[tuple] = []

    def _verify(db, chapter_id, *, nas_reachable=True):
        verified.append((chapter_id, nas_reachable))
        return {"checked": 2, "ok": 2, "mismatch": 0, "missing": 0, "skipped": 0}

    monkeypatch.setattr("app.services.audio_integrity.verify_chapter", _verify)

    worker_tasks.perform_verify_chapter_task("ch-1", run_id=run_id)

    assert probed == [], "проба NAS сходила туда, где сверке зеркало не нужно"
    assert verified == [("ch-1", True)]
    assert _statuses(sessions, run_id)[0] == "done"


def test_a_hanging_mount_skips_only_the_nas_rows_not_the_whole_job(run_row, monkeypatch):
    """Глава смешанная: есть строка на зеркале. Проба виснет — но сверка всё равно
    идёт, просто с `nas_reachable=False`, а не проваливает всё задание."""
    sessions, run_id = run_row
    monkeypatch.setattr(worker_tasks, "NAS_PROBE_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr("app.services.audio_integrity.chapter_files",
                        lambda db, chapter_id: [_Take("local"), _Take("nas")])
    monkeypatch.setattr("app.services.nas_health.probe_once", lambda root: time.sleep(30))
    verified: list[tuple] = []

    def _verify(db, chapter_id, *, nas_reachable=True):
        verified.append((chapter_id, nas_reachable))
        return {"checked": 2, "ok": 1, "mismatch": 0, "missing": 0, "skipped": 1}

    monkeypatch.setattr("app.services.audio_integrity.verify_chapter", _verify)

    worker_tasks.perform_verify_chapter_task("ch-1", run_id=run_id)

    assert verified == [("ch-1", False)]
    status, error = _statuses(sessions, run_id)
    assert status == "done", f"задание провалилось из-за NAS, хотя сверка отработала: {error}"


def test_a_mount_that_answers_lets_nas_rows_verify_too(run_row, monkeypatch):
    """Проба подтвердила, что монтирование живо, — строки на зеркале сверяются как обычно."""
    sessions, run_id = run_row
    monkeypatch.setattr(worker_tasks, "NAS_PROBE_TIMEOUT_SECONDS", 5.0)
    monkeypatch.setattr("app.services.audio_integrity.chapter_files",
                        lambda db, chapter_id: [_Take("nas")])
    monkeypatch.setattr("app.services.nas_health.probe_once", lambda root: True)
    verified: list[tuple] = []

    def _verify(db, chapter_id, *, nas_reachable=True):
        verified.append((chapter_id, nas_reachable))
        return {"checked": 1, "ok": 1, "mismatch": 0, "missing": 0, "skipped": 0}

    monkeypatch.setattr("app.services.audio_integrity.verify_chapter", _verify)

    worker_tasks.perform_verify_chapter_task("ch-1", run_id=run_id)

    assert verified == [("ch-1", True)]
    assert _statuses(sessions, run_id)[0] == "done"


def test_a_mount_that_refuses_fast_skips_nas_rows_too(run_row, monkeypatch):
    """Быстрый отказ — то же самое «недоступно», что и молчание: строки на зеркале
    остаются `skipped`, а не превращаются в `missing` и не валят задание."""
    sessions, run_id = run_row
    monkeypatch.setattr("app.services.audio_integrity.chapter_files",
                        lambda db, chapter_id: [_Take("nas")])
    monkeypatch.setattr("app.services.nas_health.probe_once", lambda root: False)
    verified: list[tuple] = []

    def _verify(db, chapter_id, *, nas_reachable=True):
        verified.append((chapter_id, nas_reachable))
        return {"checked": 1, "ok": 0, "mismatch": 0, "missing": 0, "skipped": 1}

    monkeypatch.setattr("app.services.audio_integrity.verify_chapter", _verify)

    worker_tasks.perform_verify_chapter_task("ch-1", run_id=run_id)

    assert verified == [("ch-1", False)]
    assert _statuses(sessions, run_id)[0] == "done"
