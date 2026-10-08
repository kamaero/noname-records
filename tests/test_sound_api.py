"""Ручки звуковой разметки: только редактор, коды ошибок, деньги/блокировка запуска."""
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
def editor_client(api_db):
    return _client(["author"])


@pytest.fixture()
def dictor_client(api_db):
    return _client(["dictor"])


@pytest.fixture()
def api_db(monkeypatch, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import app.models  # noqa: F401 — регистрация таблиц до create_all
    import app.v2.models  # noqa: F401
    from app.db import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.sound_api.SessionLocal", factory)
    monkeypatch.setattr("app.services.sound_engine.artifact_root", lambda: tmp_path)
    monkeypatch.setattr("app.services.sound_engine.read_credits", lambda: 5000.0)
    with factory() as db:
        build_book(db)
    return factory


SOUND_ROUTES = [
    ("get", "/api/v2/chapters/c1/sound"),
    ("post", "/api/v2/chapters/c1/sound/markers"),
    ("patch", "/api/v2/sound/markers/x"),
    ("delete", "/api/v2/sound/markers/x"),
    ("get", f"/api/v2/books/{BOOK}/sound"),
    ("patch", "/api/v2/sound/places/x"),
    ("post", "/api/v2/sound/pairs/x"),
    ("get", f"/api/v2/books/{BOOK}/sound/estimate"),
    ("post", f"/api/v2/books/{BOOK}/sound/run"),
    ("post", "/api/v2/chapters/c1/sound/run"),
    ("post", f"/api/v2/books/{BOOK}/sound/stop"),
]


def _call(client, method, path):
    call = getattr(client, method)
    return call(path, json={}) if method in ("post", "patch") else call(path)


def test_dictor_gets_403_on_every_sound_route(dictor_client):
    for method, path in SOUND_ROUTES:
        response = _call(dictor_client, method, path)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["error"] == "read_only"


def test_anonymous_gets_401_on_every_sound_route():
    client = TestClient(app)
    for method, path in SOUND_ROUTES:
        response = _call(client, method, path)
        assert response.status_code == 401, (method, path, response.text)


def test_reading_an_empty_chapter_has_no_markers(editor_client):
    response = editor_client.get("/api/v2/chapters/c1/sound")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "markers": [], "lost": [], "places": []}


def test_reading_an_unknown_chapter_is_404(editor_client):
    response = editor_client.get("/api/v2/chapters/nope/sound")
    assert response.status_code == 404
    assert response.json()["error"] == "chapter_not_found"


def test_editor_adds_edits_and_dismisses_marker(editor_client):
    added = editor_client.post(
        "/api/v2/chapters/c1/sound/markers",
        json={"kind": "sound", "segment_id": "c1:00002", "quote": "Они ушли",
              "description": "уход", "queries": ["steps away"]},
    ).json()
    assert added["ok"] is True
    marker_id = added["marker"]["id"]
    assert added["marker"]["source"] == "human"
    assert added["marker"]["kind"] == "sound"
    assert added["marker"]["payload"]["description"] == "уход"

    got = editor_client.get("/api/v2/chapters/c1/sound").json()
    assert [m["id"] for m in got["markers"]] == [marker_id]

    # перенос на другую главу — недопустимо
    bad = editor_client.patch(f"/api/v2/sound/markers/{marker_id}", json={"segment_id": "c2:00000"})
    assert bad.status_code == 400 and bad.json()["error"] == "bad_segment"

    edited = editor_client.patch(f"/api/v2/sound/markers/{marker_id}", json={"description": "тишина"})
    assert edited.status_code == 200
    assert edited.json()["marker"]["payload"]["description"] == "тишина"

    dismissed = editor_client.delete(f"/api/v2/sound/markers/{marker_id}")
    assert dismissed.status_code == 200 and dismissed.json()["ok"] is True

    after = editor_client.get("/api/v2/chapters/c1/sound").json()
    assert after["markers"] == []


def test_add_marker_bad_kind(editor_client):
    response = editor_client.post("/api/v2/chapters/c1/sound/markers",
                                  json={"kind": "не звук", "segment_id": "c1:00000"})
    assert response.status_code == 400 and response.json()["error"] == "bad_kind"


def test_add_marker_bad_segment(editor_client):
    response = editor_client.post("/api/v2/chapters/c1/sound/markers",
                                  json={"kind": "transition", "segment_id": "c1:99999", "what": "флешбэк"})
    assert response.status_code == 400 and response.json()["error"] == "bad_segment"


def test_add_marker_bad_quote(editor_client):
    response = editor_client.post(
        "/api/v2/chapters/c1/sound/markers",
        json={"kind": "sound", "segment_id": "c1:00000", "quote": "нет такой цитаты",
              "description": "звук", "queries": []},
    )
    assert response.status_code == 400 and response.json()["error"] == "bad_quote"


def test_add_marker_unknown_chapter_is_404(editor_client):
    response = editor_client.post("/api/v2/chapters/nope/sound/markers",
                                  json={"kind": "transition", "segment_id": "nope:00000"})
    assert response.status_code == 404


def test_edit_unknown_marker_is_404(editor_client):
    response = editor_client.patch("/api/v2/sound/markers/nope", json={"what": "сон"})
    assert response.status_code == 404


def test_dismiss_unknown_marker_is_404(editor_client):
    response = editor_client.delete("/api/v2/sound/markers/nope")
    assert response.status_code == 404


def test_book_sound_unknown_book_is_404(editor_client):
    response = editor_client.get("/api/v2/books/nope/sound")
    assert response.status_code == 404 and response.json()["error"] == "book_not_found"


def test_book_sound_lists_places_pairs_and_run(editor_client, api_db):
    from app.models import SoundPlace, SoundPlacePair

    with api_db() as db:
        a = SoundPlace(book_id=BOOK, name="Таверна")
        b = SoundPlace(book_id=BOOK, name="Кабак")
        db.add_all([a, b])
        db.flush()
        db.add(SoundPlacePair(book_id=BOOK, place_a=a.id, place_b=b.id, reason="одно место"))
        db.commit()

    response = editor_client.get(f"/api/v2/books/{BOOK}/sound")
    body = response.json()
    assert response.status_code == 200
    assert {p["name"] for p in body["places"]} == {"Таверна", "Кабак"}
    assert len(body["pairs"]) == 1
    assert body["run"] is None


def test_book_sound_places_include_ambience_and_chapters(editor_client, api_db):
    """F1: карточка книги должна нести то же, что и спека — подложку и главы, не только имя."""
    from app.models import SoundMarker, SoundPlace

    with api_db() as db:
        place = SoundPlace(book_id=BOOK, name="Лес", description="чаща", ambience_queries=["forest ambience"])
        db.add(place)
        db.flush()
        db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00000", kind="scene",
                           place_id=place.id, payload={}, text_sha256="h1"))
        db.add(SoundMarker(book_id=BOOK, chapter_id="c2", segment_id="c2:00000", kind="scene",
                           place_id=place.id, payload={}, text_sha256="h1"))
        db.commit()

    body = editor_client.get(f"/api/v2/books/{BOOK}/sound").json()
    assert len(body["places"]) == 1
    place_view = body["places"][0]
    assert place_view["ambience_queries"] == ["forest ambience"]
    assert place_view["chapters"] == [1, 2]  # обе главы, где место активно


def test_edit_place_ok_and_not_found(editor_client, api_db):
    from app.models import SoundPlace

    with api_db() as db:
        place = SoundPlace(book_id=BOOK, name="Лес", description="старая")
        db.add(place)
        db.commit()
        place_id = place.id

    ok = editor_client.patch(f"/api/v2/sound/places/{place_id}", json={"description": "новая"})
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    with api_db() as db:
        assert db.get(SoundPlace, place_id).description == "новая"

    missing = editor_client.patch("/api/v2/sound/places/nope", json={"description": "x"})
    assert missing.status_code == 404


def test_edit_place_rejects_blank_name(editor_client, api_db):
    """M2: пустое или пробельное имя не должно тихо стирать карточку места."""
    from app.models import SoundPlace

    with api_db() as db:
        place = SoundPlace(book_id=BOOK, name="Лес", description="старая")
        db.add(place)
        db.commit()
        place_id = place.id

    for bad_name in ("", "   "):
        response = editor_client.patch(f"/api/v2/sound/places/{place_id}", json={"name": bad_name})
        assert response.status_code == 400 and response.json()["error"] == "bad_name"

    with api_db() as db:
        assert db.get(SoundPlace, place_id).name == "Лес"  # не изменилось


def test_pair_decide_merge_apart_and_not_found(editor_client, api_db):
    from app.models import SoundPlace, SoundPlacePair

    with api_db() as db:
        a, b, c, d = (SoundPlace(book_id=BOOK, name="A"), SoundPlace(book_id=BOOK, name="B"),
                     SoundPlace(book_id=BOOK, name="C"), SoundPlace(book_id=BOOK, name="D"))
        db.add_all([a, b, c, d])
        db.flush()
        pair_merge = SoundPlacePair(book_id=BOOK, place_a=a.id, place_b=b.id, reason="1")
        pair_apart = SoundPlacePair(book_id=BOOK, place_a=c.id, place_b=d.id, reason="2")
        db.add_all([pair_merge, pair_apart])
        db.commit()
        merge_id, apart_id = pair_merge.id, pair_apart.id

    merged = editor_client.post(f"/api/v2/sound/pairs/{merge_id}", json={"merge": True})
    assert merged.status_code == 200 and merged.json() == {"ok": True, "status": "merged"}

    apart = editor_client.post(f"/api/v2/sound/pairs/{apart_id}", json={"merge": False})
    assert apart.status_code == 200 and apart.json() == {"ok": True, "status": "apart"}

    missing = editor_client.post("/api/v2/sound/pairs/nope", json={"merge": True})
    assert missing.status_code == 404


def test_pair_decide_rejects_non_bool_merge(editor_client, api_db):
    """M1: строка "false" не должна сливать, а пропущенный ключ — не «оставить порознь»."""
    from app.models import SoundPlace, SoundPlacePair

    with api_db() as db:
        a, b = SoundPlace(book_id=BOOK, name="A"), SoundPlace(book_id=BOOK, name="B")
        db.add_all([a, b])
        db.flush()
        pair = SoundPlacePair(book_id=BOOK, place_a=a.id, place_b=b.id, reason="r")
        db.add(pair)
        db.commit()
        pair_id = pair.id

    for body in ({"merge": "false"}, {"merge": 0}, {"merge": "true"}, {}):
        response = editor_client.post(f"/api/v2/sound/pairs/{pair_id}", json=body)
        assert response.status_code == 400 and response.json()["error"] == "bad_merge"

    with api_db() as db:
        assert db.get(SoundPlacePair, pair_id).status == "candidate"  # ничего не решилось


def test_estimate_has_rest_all_and_balance(editor_client):
    response = editor_client.get(f"/api/v2/books/{BOOK}/sound/estimate")
    body = response.json()
    assert response.status_code == 200
    assert body["ok"] and body["credits"] == 5000.0
    assert set(body["estimate_rub"]) == {"rest", "all"}
    assert body["blocked"] == {"rest": "", "all": ""}


def test_estimate_unknown_book_is_404(editor_client):
    response = editor_client.get("/api/v2/books/nope/sound/estimate")
    assert response.status_code == 404


def test_run_is_queued_with_its_mode(editor_client, monkeypatch):
    from app.services import sound_engine

    queued = []
    monkeypatch.setattr(sound_engine, "enqueue_sound",
                        lambda book_id, mode, chapter_id="": queued.append((book_id, mode)) or "run-1")

    response = editor_client.post(f"/api/v2/books/{BOOK}/sound/run", json={"mode": "rest"})
    assert response.status_code == 200 and response.json()["run_id"] == "run-1"
    assert queued == [(BOOK, "rest")]


def test_run_bad_mode_is_refused(editor_client):
    response = editor_client.post(f"/api/v2/books/{BOOK}/sound/run", json={"mode": "bogus"})
    assert response.status_code == 400 and response.json()["error"] == "bad_mode"


def test_run_unknown_book_is_404(editor_client):
    response = editor_client.post("/api/v2/books/nope/sound/run", json={"mode": "all"})
    assert response.status_code == 404


def test_second_run_is_refused_while_one_is_running(editor_client, api_db):
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    with api_db() as db:
        db.add(BackgroundRun(id="r1", run_key=f"sound:{BOOK}", job_kind="sound", entity_type="script_book",
                             entity_id=BOOK, status="running", meta_json="{}", created_at=utcnow_naive(),
                             updated_at=utcnow_naive()))
        db.commit()
    response = editor_client.post(f"/api/v2/books/{BOOK}/sound/run", json={"mode": "all"})
    assert response.status_code == 409 and response.json()["error"] == "already_running"


def test_a_busy_book_refuses_a_run(editor_client, api_db):
    from app.models import ScriptBook

    with api_db() as db:
        db.get(ScriptBook, BOOK).status = "processing"
        db.commit()
    response = editor_client.post(f"/api/v2/books/{BOOK}/sound/run", json={"mode": "all"})
    assert response.status_code == 409 and response.json()["error"] == "book_busy"


def test_no_credits_is_refused(editor_client, monkeypatch):
    from app.services import sound_engine
    monkeypatch.setattr(sound_engine, "read_credits", lambda: 3.0)
    response = editor_client.post(f"/api/v2/books/{BOOK}/sound/run", json={"mode": "all"})
    assert response.status_code == 400 and response.json()["error"] == "no_credits"


def test_a_consilium_run_does_not_block_a_sound_run(editor_client, api_db, monkeypatch):
    """Прогон консилиума и прогон озвучки одной книги живут в разных строках хода."""
    from app.models import BackgroundRun
    from app.services import sound_engine
    from app.time_utils import utcnow_naive

    with api_db() as db:
        db.add(BackgroundRun(id="c1run", run_key=f"consilium:{BOOK}", job_kind="consilium",
                             entity_type="script_book", entity_id=BOOK, status="running", meta_json="{}",
                             created_at=utcnow_naive(), updated_at=utcnow_naive()))
        db.commit()
    monkeypatch.setattr(sound_engine, "enqueue_sound", lambda book_id, mode, chapter_id="": "run-x")

    response = editor_client.post(f"/api/v2/books/{BOOK}/sound/run", json={"mode": "all"})
    assert response.status_code == 200 and response.json()["run_id"] == "run-x"


def test_chapter_run_queues_that_chapter_only(editor_client, monkeypatch):
    from app.services import sound_engine

    calls = []
    monkeypatch.setattr(sound_engine, "enqueue_sound",
                        lambda book_id, mode, chapter_id="": calls.append((book_id, mode, chapter_id)) or "run-c")

    response = editor_client.post("/api/v2/chapters/c1/sound/run")
    assert response.status_code == 200 and response.json()["run_id"] == "run-c"
    assert calls == [(BOOK, "chapter", "c1")]


def test_chapter_run_unknown_chapter_is_404(editor_client):
    response = editor_client.post("/api/v2/chapters/nope/sound/run")
    assert response.status_code == 404


def test_stop_reports_whether_a_run_was_stopped(editor_client, api_db):
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    assert editor_client.post(f"/api/v2/books/{BOOK}/sound/stop").json() == {"ok": True, "stopped": False}
    with api_db() as db:
        db.add(BackgroundRun(id="r1", run_key=f"sound:{BOOK}", job_kind="sound", entity_type="script_book",
                             entity_id=BOOK, status="running", meta_json=json.dumps({"phase": "chapters"}),
                             created_at=utcnow_naive(), updated_at=utcnow_naive()))
        db.commit()
    assert editor_client.post(f"/api/v2/books/{BOOK}/sound/stop").json() == {"ok": True, "stopped": True}


# --- Task 3: правка маркеров устаревляет архивную сессию главы ------------------


def _archive(api_db, chapter_id, *, delivered=False):
    from app.models import ScriptChapter
    from app.time_utils import utcnow_naive

    with api_db() as db:
        chapter = db.get(ScriptChapter, chapter_id)
        chapter.session_archived_at = utcnow_naive()
        if delivered:
            chapter.delivered_at = utcnow_naive()
        db.commit()


def _outdated(api_db, chapter_id) -> bool:
    from app.models import ScriptChapter

    with api_db() as db:
        return db.get(ScriptChapter, chapter_id).session_outdated_at is not None


def test_adding_a_marker_outdates_the_chapters_archived_session(editor_client, api_db):
    _archive(api_db, "c1")

    added = editor_client.post(
        "/api/v2/chapters/c1/sound/markers",
        json={"kind": "transition", "segment_id": "c1:00001", "what": "флешбэк"},
    )

    assert added.status_code == 200
    assert _outdated(api_db, "c1")


def test_editing_a_marker_outdates_the_chapters_archived_session(editor_client, api_db):
    from app.models import SoundMarker

    _archive(api_db, "c1")
    with api_db() as db:
        marker = SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00001", kind="transition",
                             payload={"what": "флешбэк"}, text_sha256="x")
        db.add(marker)
        db.commit()
        marker_id = marker.id

    edited = editor_client.patch(f"/api/v2/sound/markers/{marker_id}", json={"what": "сон"})

    assert edited.status_code == 200
    assert _outdated(api_db, "c1")


def test_editing_a_marker_does_not_outdate_a_delivered_chapters_session(editor_client, api_db):
    """Решение владельца «б»: сданную главу правка разметки не устаревляет."""
    from app.models import SoundMarker

    _archive(api_db, "c1", delivered=True)
    with api_db() as db:
        marker = SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00001", kind="transition",
                             payload={"what": "флешбэк"}, text_sha256="x")
        db.add(marker)
        db.commit()
        marker_id = marker.id

    edited = editor_client.patch(f"/api/v2/sound/markers/{marker_id}", json={"what": "сон"})

    assert edited.status_code == 200
    assert not _outdated(api_db, "c1")


def test_dismissing_a_marker_outdates_the_chapters_archived_session(editor_client, api_db):
    from app.models import SoundMarker

    _archive(api_db, "c1")
    with api_db() as db:
        marker = SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00001", kind="transition",
                             payload={"what": "флешбэк"}, text_sha256="x")
        db.add(marker)
        db.commit()
        marker_id = marker.id

    dismissed = editor_client.delete(f"/api/v2/sound/markers/{marker_id}")

    assert dismissed.status_code == 200
    assert _outdated(api_db, "c1")


def test_editing_a_place_outdates_every_chapter_where_it_is_active(editor_client, api_db):
    from app.models import SoundMarker, SoundPlace

    with api_db() as db:
        place = SoundPlace(book_id=BOOK, name="Лес", description="чаща")
        db.add(place)
        db.flush()
        db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00000", kind="scene",
                           place_id=place.id, payload={}, text_sha256="x"))
        db.add(SoundMarker(book_id=BOOK, chapter_id="c2", segment_id="c2:00000", kind="scene",
                           place_id=place.id, payload={}, text_sha256="x"))
        db.commit()
        place_id = place.id
    _archive(api_db, "c1")
    _archive(api_db, "c2")

    response = editor_client.patch(f"/api/v2/sound/places/{place_id}", json={"description": "новая"})

    assert response.status_code == 200
    assert _outdated(api_db, "c1")
    assert _outdated(api_db, "c2")


def test_editing_a_place_does_not_touch_chapters_without_its_markers(editor_client, api_db):
    from app.models import SoundPlace

    with api_db() as db:
        place = SoundPlace(book_id=BOOK, name="Лес", description="чаща")
        db.add(place)
        db.commit()
        place_id = place.id
    _archive(api_db, "c1")

    response = editor_client.patch(f"/api/v2/sound/places/{place_id}", json={"description": "новая"})

    assert response.status_code == 200
    assert not _outdated(api_db, "c1")


def test_merging_a_pair_outdates_chapters_of_both_places(editor_client, api_db):
    from app.models import SoundMarker, SoundPlace, SoundPlacePair

    with api_db() as db:
        a = SoundPlace(book_id=BOOK, name="Таверна")
        b = SoundPlace(book_id=BOOK, name="Кабак")
        db.add_all([a, b])
        db.flush()
        db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00000", kind="scene",
                           place_id=a.id, payload={}, text_sha256="x"))
        db.add(SoundMarker(book_id=BOOK, chapter_id="c2", segment_id="c2:00000", kind="scene",
                           place_id=b.id, payload={}, text_sha256="x"))
        pair = SoundPlacePair(book_id=BOOK, place_a=a.id, place_b=b.id, reason="одно место")
        db.add(pair)
        db.commit()
        pair_id = pair.id
    _archive(api_db, "c1")
    _archive(api_db, "c2")

    response = editor_client.post(f"/api/v2/sound/pairs/{pair_id}", json={"merge": True})

    assert response.status_code == 200 and response.json()["status"] == "merged"
    assert _outdated(api_db, "c1")
    assert _outdated(api_db, "c2")


def test_reading_a_chapter_after_a_text_change_outdates_its_archived_session(editor_client, api_db):
    """F2: `GET .../sound` перепривязывает маркеры к новому тексту главы (переезд или
    потеря) — архивный проект (если есть) с этого момента ему не соответствует."""
    from app.models import SoundMarker
    from app.v2.models import V2Segment

    _archive(api_db, "c1")
    with api_db() as db:
        db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00001", kind="transition",
                           payload={"what": "флешбэк"}, text_sha256="stale-sha"))
        seg = db.query(V2Segment).filter_by(chapter_id="c1", ordinal=2).one()
        seg.text = "Они ушли не сразу, а после долгой паузы."
        db.commit()

    response = editor_client.get("/api/v2/chapters/c1/sound")

    assert response.status_code == 200
    assert _outdated(api_db, "c1")


def test_reading_a_chapter_after_a_text_change_does_not_outdate_a_delivered_chapter(editor_client, api_db):
    """F2 + решение владельца «б»: сданную главу переезд маркеров не устаревляет."""
    from app.models import SoundMarker
    from app.v2.models import V2Segment

    _archive(api_db, "c1", delivered=True)
    with api_db() as db:
        db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00001", kind="transition",
                           payload={"what": "флешбэк"}, text_sha256="stale-sha"))
        seg = db.query(V2Segment).filter_by(chapter_id="c1", ordinal=2).one()
        seg.text = "Они ушли не сразу, а после долгой паузы."
        db.commit()

    response = editor_client.get("/api/v2/chapters/c1/sound")

    assert response.status_code == 200
    assert not _outdated(api_db, "c1")


def test_reading_a_chapter_with_no_text_change_does_not_touch_its_session(editor_client, api_db):
    """F2, обратная сторона: чтение без переезда (текст не менялся) не должно
    поднимать `session_outdated_at` попусту на каждом открытии вкладки."""
    _archive(api_db, "c1")

    response = editor_client.get("/api/v2/chapters/c1/sound")

    assert response.status_code == 200
    assert not _outdated(api_db, "c1")


def test_deciding_a_pair_apart_does_not_touch_sessions(editor_client, api_db):
    from app.models import SoundMarker, SoundPlace, SoundPlacePair

    with api_db() as db:
        a = SoundPlace(book_id=BOOK, name="X")
        b = SoundPlace(book_id=BOOK, name="Y")
        db.add_all([a, b])
        db.flush()
        db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00000", kind="scene",
                           place_id=a.id, payload={}, text_sha256="x"))
        pair = SoundPlacePair(book_id=BOOK, place_a=a.id, place_b=b.id, reason="r")
        db.add(pair)
        db.commit()
        pair_id = pair.id
    _archive(api_db, "c1")

    response = editor_client.post(f"/api/v2/sound/pairs/{pair_id}", json={"merge": False})

    assert response.status_code == 200 and response.json()["status"] == "apart"
    assert not _outdated(api_db, "c1")


def test_an_exhausted_month_refuses_sound_runs(api_db, editor_client):
    from tests.spend_helpers import exhaust_month
    exhaust_month(api_db)
    assert editor_client.post(f"/api/v2/books/{BOOK}/sound/run", json={"mode": "all"}).status_code == 409
    assert editor_client.post("/api/v2/chapters/c1/sound/run").status_code == 409
