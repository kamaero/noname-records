"""Ручки прогона: смета, запуск, остановка — только тому, кто правит разметку."""
import json

import pytest
from fastapi.testclient import TestClient

from app.auth import session_serializer
from app.main import app
from tests.consilium_book import BOOK, build_book


def _client(roles):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": "u1", "sub": "u1", "roles": roles, "display_name": "Кто-то"}))
    return client


@pytest.fixture()
def api_db(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.models  # noqa: F401
    import app.v2.models  # noqa: F401
    from app.db import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.api.SessionLocal", factory)
    monkeypatch.setattr("app.services.consilium_engine.ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setattr("app.services.consilium_engine.read_credits", lambda log=None: 5000.0)
    queued = []
    monkeypatch.setattr("app.services.consilium_engine.enqueue_consilium",
                        lambda book_id, mode: queued.append((book_id, mode)) or "run-x")
    with factory() as db:
        build_book(db)
    return factory, queued


def test_a_dictor_cannot_see_the_estimate_or_start_a_run(api_db):
    client = _client(["dictor"])
    assert client.get(f"/api/v2/books/{BOOK}/consilium/estimate").status_code == 403
    assert client.post(f"/api/v2/books/{BOOK}/consilium/run", json={"mode": "recheck"}).status_code == 403
    assert client.post(f"/api/v2/books/{BOOK}/consilium/stop").status_code == 403


def test_estimate_has_both_modes_and_balance(api_db):
    body = _client(["author"]).get(f"/api/v2/books/{BOOK}/consilium/estimate").json()
    assert body["ok"] and body["credits"] == 5000.0
    assert set(body["estimate_rub"]) == {"recheck", "reread"}
    assert body["blocked"] == {"recheck": "", "reread": ""}


def test_run_is_queued_with_its_mode(api_db):
    _factory, queued = api_db
    response = _client(["author"]).post(f"/api/v2/books/{BOOK}/consilium/run", json={"mode": "reread"})
    assert response.status_code == 200 and response.json()["run_id"] == "run-x"
    assert queued == [(BOOK, "reread")]


def test_run_estimates_off_the_event_loop(api_db, monkeypatch):
    import asyncio

    import app.services.consilium_engine as engine

    real, seen = engine.estimate, []

    def recording(db, book_id, **kwargs):
        try:
            asyncio.get_running_loop()
            seen.append("event loop")
        except RuntimeError:
            seen.append("thread")
        return real(db, book_id, **kwargs)

    monkeypatch.setattr(engine, "estimate", recording)
    response = _client(["author"]).post(f"/api/v2/books/{BOOK}/consilium/run", json={"mode": "recheck"})
    assert response.status_code == 200
    assert seen == ["thread"]  # смета большой книги не морозит сайт


@pytest.mark.parametrize("body,code", [({"mode": "всё"}, "bad_mode"), ({}, "bad_mode")])
def test_bad_mode_is_refused(api_db, body, code):
    response = _client(["author"]).post(f"/api/v2/books/{BOOK}/consilium/run", json=body)
    assert response.status_code == 400 and response.json()["error"] == code


def test_a_second_run_and_a_busy_book_are_refused(api_db):
    factory, queued = api_db
    from app.models import BackgroundRun, ScriptBook
    from app.time_utils import utcnow_naive

    with factory() as db:
        db.add(BackgroundRun(id="r1", run_key=f"consilium:{BOOK}", job_kind="consilium", entity_type="script_book",
                             entity_id=BOOK, status="running", meta_json="{}", created_at=utcnow_naive(),
                             updated_at=utcnow_naive()))
        db.commit()
    first = _client(["author"]).post(f"/api/v2/books/{BOOK}/consilium/run", json={"mode": "recheck"})
    assert first.status_code == 409 and first.json()["error"] == "already_running"
    with factory() as db:
        db.query(BackgroundRun).delete()
        db.get(ScriptBook, BOOK).status = "processing"
        db.commit()
    busy = _client(["author"]).post(f"/api/v2/books/{BOOK}/consilium/run", json={"mode": "recheck"})
    assert busy.status_code == 409 and busy.json()["error"] == "book_busy"
    assert queued == []


def test_no_credits_is_refused(api_db, monkeypatch):
    monkeypatch.setattr("app.services.consilium_engine.read_credits", lambda log=None: 3.0)
    response = _client(["author"]).post(f"/api/v2/books/{BOOK}/consilium/run", json={"mode": "reread"})
    assert response.status_code == 400 and response.json()["error"] == "no_credits"


def test_stop_and_run_status_in_the_findings_list(api_db):
    factory, _queued = api_db
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    client = _client(["author"])
    assert client.post(f"/api/v2/books/{BOOK}/consilium/stop").json() == {"ok": True, "stopped": False}
    with factory() as db:
        db.add(BackgroundRun(id="r1", run_key=f"consilium:{BOOK}", job_kind="consilium", entity_type="script_book",
                             entity_id=BOOK, status="running", meta_json=json.dumps({"phase": "readers"}),
                             created_at=utcnow_naive(), updated_at=utcnow_naive()))
        db.commit()
    assert client.post(f"/api/v2/books/{BOOK}/consilium/stop").json() == {"ok": True, "stopped": True}
    run = client.get(f"/api/v2/books/{BOOK}/consilium").json()["run"]
    assert run["status"] == "running" and run["stop_requested"] is True


def test_a_dictor_does_not_see_money_in_the_findings_list(api_db):
    factory, _queued = api_db
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    with factory() as db:
        db.add(BackgroundRun(id="r1", run_key=f"consilium:{BOOK}", job_kind="consilium", entity_type="script_book",
                             entity_id=BOOK, status="running",
                             meta_json=json.dumps({"phase": "readers", "spent_rub": 42.0}),
                             created_at=utcnow_naive(), updated_at=utcnow_naive()))
        db.commit()
    dictor_run = _client(["dictor"]).get(f"/api/v2/books/{BOOK}/consilium").json()["run"]
    assert dictor_run is None
    author_run = _client(["author"]).get(f"/api/v2/books/{BOOK}/consilium").json()["run"]
    assert author_run["spent_rub"] == 42.0


def test_a_dead_run_does_not_block_the_buttons_forever(api_db):
    from datetime import timedelta

    factory, queued = api_db
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    old = utcnow_naive() - timedelta(minutes=61)
    with factory() as db:
        db.add(BackgroundRun(id="r1", run_key=f"consilium:{BOOK}", job_kind="consilium", entity_type="script_book",
                             entity_id=BOOK, status="running", meta_json=json.dumps({"phase": "readers"}),
                             created_at=old, updated_at=old, started_at=old, heartbeat_at=old))
        db.commit()
    client = _client(["author"])
    body = client.get(f"/api/v2/books/{BOOK}/consilium/estimate").json()
    assert body["blocked"] == {"recheck": "", "reread": ""}
    run = client.get(f"/api/v2/books/{BOOK}/consilium").json()["run"]
    assert run["status"] == "failed" and run["error"] == "воркер остановился — прогон можно продолжить"
    with factory() as db:
        assert db.get(BackgroundRun, "r1").status == "failed"  # отметка записана, а не только показана
    assert client.post(f"/api/v2/books/{BOOK}/consilium/run", json={"mode": "recheck"}).status_code == 200
    assert queued == [(BOOK, "recheck")]


def test_a_dead_run_is_recorded_even_when_only_the_list_is_opened(api_db):
    from datetime import timedelta

    factory, _queued = api_db
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    old = utcnow_naive() - timedelta(minutes=61)
    with factory() as db:
        db.add(BackgroundRun(id="r1", run_key=f"consilium:{BOOK}", job_kind="consilium", entity_type="script_book",
                             entity_id=BOOK, status="running", meta_json="{}", created_at=old, updated_at=old,
                             started_at=old, heartbeat_at=old))
        db.commit()
    assert _client(["author"]).get(f"/api/v2/books/{BOOK}/consilium").json()["run"]["status"] == "failed"
    with factory() as db:
        assert db.get(BackgroundRun, "r1").status == "failed"


def test_stop_on_a_queued_run_stops_it_at_once(api_db):
    factory, _queued = api_db
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    with factory() as db:
        db.add(BackgroundRun(id="r1", run_key=f"consilium:{BOOK}", job_kind="consilium", entity_type="script_book",
                             entity_id=BOOK, status="queued", meta_json=json.dumps({"phase": "queued"}),
                             created_at=utcnow_naive(), updated_at=utcnow_naive()))
        db.commit()
    client = _client(["author"])
    assert client.post(f"/api/v2/books/{BOOK}/consilium/stop").json() == {"ok": True, "stopped": True}
    run = client.get(f"/api/v2/books/{BOOK}/consilium").json()["run"]
    assert run["status"] == "stopped" and run["error"] == "остановлен вручную"


def test_an_exhausted_month_refuses_the_run_with_the_numbers(api_db):
    from tests.spend_helpers import exhaust_month
    factory, queued = api_db
    exhaust_month(factory)
    response = _client(["author"]).post(f"/api/v2/books/{BOOK}/consilium/run", json={"mode": "reread"})
    assert response.status_code == 409 and response.json()["error"] == "over_limit"
    assert response.json()["left_rub"] == 0 and response.json()["limit_rub"] == 1 and queued == []


def test_only_an_admin_runs_over_the_limit(api_db):
    from tests.spend_helpers import exhaust_month
    factory, queued = api_db
    exhaust_month(factory)
    path = f"/api/v2/books/{BOOK}/consilium/run?override_limit=1"
    assert _client(["author"]).post(path, json={"mode": "reread"}).status_code == 403
    assert _client(["admin"]).post(path, json={"mode": "reread"}).status_code == 200 and queued
