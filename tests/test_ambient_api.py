"""Ручки эмбиента и его поля в строке ASR: только редактор, коды отказов, проверка
идущего прогона, поля строки главы, эмбиент сцены в карточке.

Ни один тест не ставит задачу в настоящую очередь и не ходит в ElevenLabs: постановка
подменена, генерация — подменёнными Opus/ElevenLabs движка (`tests.test_ambient_engine`).
"""
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import session_serializer
from app.main import app
from app.models import AmbientTrack, AsrJob, AudioFile, BackgroundRun
from app.services.sound_store import add_marker
from app.time_utils import utcnow_naive
from tests.consilium_book import BOOK
from tests.test_ambient_engine import MP3, Compose, _go, _record, _scenes, factory  # noqa: F401 — фикстура

UNPRICED = ("У модели этого шага нет цены: траты прогона не войдут в лимит. "
            "Впишите цену в «Настройки → Нейросети».")


def _client(roles):
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps(
        {"uid": "u1", "sub": "u1", "roles": roles, "display_name": "Кто-то"}))
    return client


class Queue:
    """Подменённая постановка прогона: запоминает вызовы, отвечает `answer`."""

    def __init__(self, answer="run-1"):
        self.answer = answer
        self.calls: list[tuple] = []

    def __call__(self, chapter_id, marker_id="", prompt_override=""):
        self.calls.append((chapter_id, marker_id, prompt_override))
        return self.answer


@pytest.fixture()
def api(factory, monkeypatch):  # noqa: F811
    from app.config import settings

    monkeypatch.setattr("app.v2.ambient_api.SessionLocal", factory)
    monkeypatch.setattr("app.v2.api.SessionLocal", factory)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    queue = Queue()
    monkeypatch.setattr("app.services.ambient_engine.enqueue_ambient", queue)
    return queue


@pytest.fixture()
def editor(api):
    return _client(["author"])


def _run_row(factory, status, *, meta=None, age_minutes=0):  # noqa: F811
    now = utcnow_naive() - timedelta(minutes=age_minutes)
    with factory() as db:
        run = BackgroundRun(run_key="ambient:c1", job_kind="ambient", status=status,
                            meta_json=json.dumps(meta or {}), created_at=now, started_at=now, heartbeat_at=now)
        db.add(run)
        db.commit()
        return run.id


ROUTES = [
    ("post", "/api/v2/chapters/c1/ambient"),
    ("get", "/api/v2/chapters/c1/ambient/plan"),
    ("post", "/api/v2/chapters/c1/ambient/stop"),
    ("post", "/api/v2/sound/markers/x/ambient"),
    ("get", "/api/v2/ambient/x/audio"),
]


def _call(client, method, path, **kwargs):
    if method == "post":
        return client.post(path, json=kwargs.get("json", {}))
    return client.get(path, headers=kwargs.get("headers"))


class TestOnlyTheEditor:
    def test_a_dictor_gets_403_on_every_route(self, api):
        dictor = _client(["dictor"])
        for method, path in ROUTES:
            response = _call(dictor, method, path)
            assert response.status_code == 403, (method, path, response.text)
            assert response.json()["error"] == "read_only"

    def test_anonymous_gets_401_on_every_route(self, api):
        client = TestClient(app)
        for method, path in ROUTES:
            assert _call(client, method, path).status_code == 401, (method, path)

    def test_the_dictor_sees_no_ambient_in_the_asr_row(self, api, factory):  # noqa: F811
        _scenes(factory)
        editor_row = _client(["author"]).get(f"/api/v2/books/{BOOK}/recording").json()["chapters"][0]
        dictor_row = _client(["dictor"]).get(f"/api/v2/books/{BOOK}/recording").json()["chapters"][0]

        assert editor_row["ambient_todo"] == 3
        assert (dictor_row["ambient_todo"], dictor_row["ambient_state"]) == (0, "")


class TestChapterRoute:
    def test_starts_the_generation(self, editor, api, factory):  # noqa: F811
        _scenes(factory)

        response = editor.post("/api/v2/chapters/c1/ambient")

        assert response.status_code == 200
        # у музыки ElevenLabs нет встроенной цены: запуск честно предупреждает, что лимит её не видит
        assert response.json() == {"ok": True, "run_id": "run-1", "spend_warning": UNPRICED}
        assert api.calls == [("c1", "", "")]

    def test_an_unknown_chapter_is_404(self, editor):
        response = editor.post("/api/v2/chapters/nope/ambient")
        assert response.status_code == 404 and response.json()["error"] == "chapter_not_found"

    def test_no_key_is_400(self, editor, api, factory, monkeypatch):  # noqa: F811
        from app.config import settings

        _scenes(factory)
        monkeypatch.setattr(settings, "elevenlabs_api_key", "")

        response = editor.post("/api/v2/chapters/c1/ambient")

        assert response.status_code == 400 and response.json()["error"] == "no_key"
        assert api.calls == []
        assert "test-key" not in response.text

    def test_an_unrecorded_chapter_is_not_ready(self, editor, api):
        response = editor.post("/api/v2/chapters/c2/ambient")
        assert response.status_code == 400 and response.json()["error"] == "not_ready"
        assert api.calls == []

    def test_a_chapter_without_scenes_has_nothing_to_do(self, editor, api):
        response = editor.post("/api/v2/chapters/c1/ambient")
        assert response.status_code == 400 and response.json()["error"] == "nothing_to_do"

    def test_a_fully_generated_chapter_has_nothing_to_do(self, editor, api, factory):  # noqa: F811
        _scenes(factory)
        _go(factory)

        response = editor.post("/api/v2/chapters/c1/ambient")

        assert response.status_code == 400 and response.json()["error"] == "nothing_to_do"

    @pytest.mark.parametrize("status", ["queued", "running"])
    def test_a_live_run_is_409(self, editor, api, factory, status):  # noqa: F811
        _scenes(factory)
        _run_row(factory, status)

        response = editor.post("/api/v2/chapters/c1/ambient")

        assert response.status_code == 409 and response.json()["error"] == "already_running"
        assert api.calls == []

    def test_a_dead_run_does_not_block(self, editor, api, factory):  # noqa: F811
        _scenes(factory)
        run_id = _run_row(factory, "running", age_minutes=120)

        response = editor.post("/api/v2/chapters/c1/ambient")

        assert response.status_code == 200
        with factory() as db:
            assert db.get(BackgroundRun, run_id).status == "failed", "мёртвый прогон отмечен"

    def test_a_lost_enqueue_without_a_run_is_503(self, editor, api, factory):  # noqa: F811
        _scenes(factory)
        api.answer = None

        response = editor.post("/api/v2/chapters/c1/ambient")

        assert response.status_code == 503 and response.json()["error"] == "queue_unavailable"
        assert response.json()["detail"] == "Очередь недоступна, попробуйте позже"

    def test_a_deduplicated_enqueue_with_a_live_run_is_409(self, editor, api, factory, monkeypatch):  # noqa: F811
        _scenes(factory)

        def racing(chapter_id, marker_id="", prompt_override=""):
            _run_row(factory, "queued")  # соседний запрос успел поставить прогон
            return None

        monkeypatch.setattr("app.services.ambient_engine.enqueue_ambient", racing)

        response = editor.post("/api/v2/chapters/c1/ambient")

        assert response.status_code == 409 and response.json()["error"] == "already_running"


class TestStopRoute:
    def test_a_running_run_is_asked_to_stop(self, editor, factory):  # noqa: F811
        run_id = _run_row(factory, "running")

        response = editor.post("/api/v2/chapters/c1/ambient/stop")

        assert response.status_code == 200 and response.json() == {"ok": True, "stopped": True}
        with factory() as db:
            run = db.get(BackgroundRun, run_id)
            assert run.status == "running", "текущий трек доделается"
            assert json.loads(run.meta_json)["stop_requested"] is True

    def test_a_queued_run_whose_job_was_lost_is_cleared(self, editor, api, factory):  # noqa: F811
        _scenes(factory)
        run_id = _run_row(factory, "queued", age_minutes=600)
        assert editor.post("/api/v2/chapters/c1/ambient").status_code == 409

        response = editor.post("/api/v2/chapters/c1/ambient/stop")

        assert response.json()["stopped"] is True
        with factory() as db:
            assert db.get(BackgroundRun, run_id).status == "stopped"
        assert editor.post("/api/v2/chapters/c1/ambient").status_code == 200, "кнопка снова открыта"

    def test_nothing_to_stop(self, editor):
        response = editor.post("/api/v2/chapters/c1/ambient/stop")
        assert response.status_code == 200 and response.json() == {"ok": True, "stopped": False}

    def test_an_unknown_chapter_is_404(self, editor):
        assert editor.post("/api/v2/chapters/nope/ambient/stop").status_code == 404

    def test_only_this_chapter_is_stopped(self, editor, factory):  # noqa: F811
        with factory() as db:
            other = BackgroundRun(run_key="ambient:c2", job_kind="ambient", status="running", meta_json="{}")
            db.add(other)
            db.commit()
            other_id = other.id

        assert editor.post("/api/v2/chapters/c1/ambient/stop").json()["stopped"] is False
        with factory() as db:
            assert "stop_requested" not in json.loads(db.get(BackgroundRun, other_id).meta_json)


class TestSceneRoute:
    def test_regenerates_one_scene_with_the_edited_prompt(self, editor, api, factory):  # noqa: F811
        ids = _scenes(factory)
        _go(factory)

        response = editor.post(f"/api/v2/sound/markers/{ids[1]}/ambient", json={"prompt": "  soft harp  "})

        assert response.status_code == 200 and response.json() == {"ok": True, "run_id": "run-1", "spend_warning": UNPRICED}
        assert api.calls == [("c1", ids[1], "soft harp")]

    def test_an_exhausted_month_refuses_a_scene_too(self, editor, api, factory):  # noqa: F811
        """Иначе лимит обходился бы перегенерацией по одной сцене."""
        from tests.spend_helpers import exhaust_month
        ids = _scenes(factory)
        exhaust_month(factory)
        response = editor.post(f"/api/v2/sound/markers/{ids[1]}/ambient", json={"prompt": "harp"})
        assert response.status_code == 409 and api.calls == []

    def test_no_prompt_means_a_fresh_one(self, editor, api, factory):  # noqa: F811
        ids = _scenes(factory)

        response = editor.post(f"/api/v2/sound/markers/{ids[0]}/ambient", json={})

        assert response.status_code == 200
        assert api.calls == [("c1", ids[0], "")]

    def test_a_non_string_prompt_is_400(self, editor, api, factory):  # noqa: F811
        ids = _scenes(factory)
        response = editor.post(f"/api/v2/sound/markers/{ids[0]}/ambient", json={"prompt": ["x"]})
        assert response.status_code == 400 and response.json()["error"] == "bad_prompt"

    def test_an_unknown_marker_is_404(self, editor, api):
        response = editor.post("/api/v2/sound/markers/nope/ambient", json={})
        assert response.status_code == 404

    def test_a_sound_marker_is_not_a_scene(self, editor, api, factory):  # noqa: F811
        with factory() as db:
            row = add_marker(db, chapter_id="c1", segment_id="c1:00002", kind="sound",
                             fields={"quote": "Они ушли", "description": "уход"})
            db.commit()
            marker_id = row.id

        response = editor.post(f"/api/v2/sound/markers/{marker_id}/ambient", json={})

        assert response.status_code == 400 and response.json()["error"] == "not_scene"

    def test_a_scene_without_a_place_on_the_timeline_is_refused(self, editor, api, factory):  # noqa: F811
        with factory() as db:
            db.query(AsrJob).delete()
            db.query(AudioFile).delete()
            db.commit()
            _record(db, last_line_matched=False)
        ids = _scenes(factory)

        response = editor.post(f"/api/v2/sound/markers/{ids[2]}/ambient", json={})

        assert response.status_code == 400 and response.json()["error"] == "not_placed"
        assert api.calls == []

    def test_a_live_run_is_409(self, editor, api, factory):  # noqa: F811
        ids = _scenes(factory)
        _run_row(factory, "running")

        response = editor.post(f"/api/v2/sound/markers/{ids[0]}/ambient", json={"prompt": "x"})

        assert response.status_code == 409 and response.json()["error"] == "already_running"
        assert api.calls == []

    def test_a_lost_enqueue_without_a_run_is_503(self, editor, api, factory):  # noqa: F811
        ids = _scenes(factory)
        api.answer = None

        response = editor.post(f"/api/v2/sound/markers/{ids[0]}/ambient", json={})

        assert response.status_code == 503 and response.json()["error"] == "queue_unavailable"

    def test_a_too_long_prompt_is_400(self, editor, api, factory):  # noqa: F811
        ids = _scenes(factory)

        response = editor.post(f"/api/v2/sound/markers/{ids[0]}/ambient", json={"prompt": "x" * 2001})

        assert response.status_code == 400 and response.json()["error"] == "bad_prompt"
        assert api.calls == []
        ok = editor.post(f"/api/v2/sound/markers/{ids[0]}/ambient", json={"prompt": "x" * 2000})
        assert ok.status_code == 200

    def test_no_key_is_400(self, editor, api, factory, monkeypatch):  # noqa: F811
        from app.config import settings

        ids = _scenes(factory)
        monkeypatch.setattr(settings, "elevenlabs_api_key", " ")

        response = editor.post(f"/api/v2/sound/markers/{ids[0]}/ambient", json={})

        assert response.status_code == 400 and response.json()["error"] == "no_key"


class TestAudioRoute:
    def _ambient_id(self, factory):  # noqa: F811
        _scenes(factory)
        _go(factory)
        with factory() as db:
            return db.query(AudioFile).filter(AudioFile.kind == "ambient").order_by(AudioFile.stored_key).first().id

    def test_streams_the_track(self, editor, factory):  # noqa: F811
        audio_id = self._ambient_id(factory)

        response = editor.get(f"/api/v2/ambient/{audio_id}/audio")

        assert response.status_code == 200
        assert response.content.startswith(MP3)
        assert response.headers["content-type"] == "audio/mpeg"
        assert response.headers["accept-ranges"] == "bytes"

    def test_answers_a_range(self, editor, factory):  # noqa: F811
        audio_id = self._ambient_id(factory)

        response = editor.get(f"/api/v2/ambient/{audio_id}/audio", headers={"Range": "bytes=0-2"})

        assert response.status_code == 206
        assert response.content == MP3[:3]
        assert response.headers["content-range"].startswith("bytes 0-2/")

    def test_a_take_is_not_reachable_here(self, editor, factory):  # noqa: F811
        with factory() as db:
            take_id = db.query(AudioFile).filter(AudioFile.kind == "take").first().id

        response = editor.get(f"/api/v2/ambient/{take_id}/audio")

        assert response.status_code == 404 and response.json()["error"] == "ambient_not_found"


def _row(factory, chapter_id="c1", ambient=True):  # noqa: F811
    from app.services.chapter_delivery import book_recording_status

    with factory() as db:
        status = book_recording_status(db, BOOK, ambient=ambient)
    return next(row for row in status["chapters"] if row["chapter_id"] == chapter_id)


def _fields(row):
    return {key: row[key] for key in ("ambient_todo", "ambient_done", "ambient_failed", "ambient_skipped",
                                      "ambient_state")}


class TestAsrRowFields:
    def test_scenes_waiting_for_a_track(self, factory):  # noqa: F811
        _scenes(factory)
        assert _fields(_row(factory)) == {"ambient_todo": 3, "ambient_done": 0, "ambient_failed": 0,
                                          "ambient_skipped": 0, "ambient_state": ""}
        assert "ambient_todo_minutes" not in _row(factory), "минуты — в смете, не в строке"

    def test_without_the_flag_the_fields_are_empty(self, factory):  # noqa: F811
        _scenes(factory)
        assert _fields(_row(factory, ambient=False)) == {"ambient_todo": 0, "ambient_done": 0,
                                                         "ambient_failed": 0, "ambient_skipped": 0,
                                                         "ambient_state": ""}

    def test_an_unrecorded_chapter_counts_nothing(self, factory):  # noqa: F811
        with factory() as db:
            add_marker(db, chapter_id="c2", segment_id="c2:00000", kind="scene", fields={"mood": "тишь"})
            db.commit()
        assert _fields(_row(factory, "c2"))["ambient_todo"] == 0

    def test_a_generated_chapter_is_done(self, factory):  # noqa: F811
        _scenes(factory)
        _go(factory)
        assert _fields(_row(factory)) == {"ambient_todo": 0, "ambient_done": 3, "ambient_failed": 0,
                                          "ambient_skipped": 0, "ambient_state": "done"}

    def test_a_live_run_is_running(self, factory):  # noqa: F811
        _scenes(factory)
        _run_row(factory, "running", meta={"tracks_total": 3, "tracks_done": 1})
        assert _row(factory)["ambient_state"] == "running"

    def test_a_silent_run_is_not_running(self, factory):  # noqa: F811
        _scenes(factory)
        _run_row(factory, "running", age_minutes=120)
        assert _row(factory)["ambient_state"] == "failed"

    def test_quota_stops_with_what_is_done(self, factory):  # noqa: F811
        from app.services.elevenlabs_client import QuotaExhausted

        _scenes(factory)
        _go(factory, compose=Compose([None, QuotaExhausted("402")]))

        fields = _fields(_row(factory))
        assert fields["ambient_state"] == "quota"
        assert (fields["ambient_done"], fields["ambient_todo"]) == (1, 2)

    def test_a_failed_track_marks_the_row(self, factory):  # noqa: F811
        _scenes(factory)
        _go(factory, compose=Compose([None, RuntimeError("net"), RuntimeError("net")]))

        fields = _fields(_row(factory))
        assert fields["ambient_state"] == "failed"
        assert (fields["ambient_failed"], fields["ambient_done"], fields["ambient_todo"]) == (1, 2, 1)

    def test_a_rejected_key_is_its_own_state(self, factory):  # noqa: F811
        from app.services.elevenlabs_client import BadKey

        _scenes(factory)
        _go(factory, compose=Compose([BadKey("401", status=401)]))

        assert _row(factory)["ambient_state"] == "bad_key"

    def test_disk_full_is_its_own_state(self, factory):  # noqa: F811
        _scenes(factory)
        _run_row(factory, "done", meta={"result": {"status": "disk_full"}})
        assert _row(factory)["ambient_state"] == "disk_full"

    def test_a_dismissed_scene_track_is_not_counted_done(self, factory):  # noqa: F811
        from app.services.sound_store import dismiss_marker

        ids = _scenes(factory)
        _go(factory)
        with factory() as db:
            dismiss_marker(db, ids[0])
            db.commit()
        assert _row(factory)["ambient_done"] == 2


    def test_the_row_does_not_lay_out_the_timeline(self, factory, monkeypatch):  # noqa: F811
        from app.services import ambient_engine, chapter_delivery

        _scenes(factory)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("строка ASR не раскладывает главу по таймлайну")

        monkeypatch.setattr(chapter_delivery, "_spans_on_timeline", forbidden)
        monkeypatch.setattr(ambient_engine, "ambient_plan", forbidden)
        monkeypatch.setattr(ambient_engine, "_chapter_scenes", forbidden)
        real_layout = chapter_delivery._script_layout
        calls = []
        monkeypatch.setattr(chapter_delivery, "_script_layout",
                            lambda *a, **k: calls.append(a) or real_layout(*a, **k))

        assert _row(factory)["ambient_todo"] == 3
        assert calls == []

    def _unplaced_last_scene(self, factory):  # noqa: F811
        with factory() as db:
            db.query(AsrJob).delete()
            db.query(AudioFile).delete()
            db.commit()
            _record(db, last_line_matched=False)
        return _scenes(factory)

    def test_a_scene_without_a_place_does_not_hold_back_done(self, factory):  # noqa: F811
        self._unplaced_last_scene(factory)
        _go(factory)

        fields = _row(factory)
        assert (fields["ambient_done"], fields["ambient_todo"], fields["ambient_skipped"]) == (2, 1, 1)
        assert fields["ambient_state"] == "done"

    def test_a_scene_added_after_the_run_is_not_done(self, factory):  # noqa: F811
        self._unplaced_last_scene(factory)
        _go(factory)
        with factory() as db:
            add_marker(db, chapter_id="c1", segment_id="c1:00001", kind="scene", fields={"mood": "новая"})
            db.commit()

        fields = _row(factory)
        assert (fields["ambient_todo"], fields["ambient_skipped"]) == (2, 1)
        assert fields["ambient_state"] == ""

    def test_a_scene_regeneration_keeps_the_chapter_run_skipped(self, factory):  # noqa: F811
        ids = self._unplaced_last_scene(factory)
        _go(factory)
        _go(factory, marker_id=ids[0])

        fields = _row(factory)
        assert fields["ambient_skipped"] == 1, "берётся последний прогон по всей главе"
        assert fields["ambient_state"] == "done"

    def test_only_the_newest_run_decides(self, factory):  # noqa: F811
        _scenes(factory)
        _run_row(factory, "failed", meta={"marker_id": "", "tracks_failed": 2}, age_minutes=30)
        _go(factory)

        fields = _row(factory)
        assert (fields["ambient_failed"], fields["ambient_state"]) == (0, "done")

    def test_a_deleted_file_makes_the_scene_wait_again(self, factory):  # noqa: F811
        from app.services.ambient_engine import ambient_plan
        from app.services.chapter_delivery import chapter_ambient_files

        ids = _scenes(factory)
        _go(factory)
        with factory() as db:
            track = db.query(AmbientTrack).filter(AmbientTrack.marker_id == ids[1]).one()
            db.delete(db.get(AudioFile, track.audio_file_id))
            db.commit()

        fields = _row(factory)
        assert (fields["ambient_done"], fields["ambient_todo"], fields["ambient_state"]) == (2, 1, "")
        with factory() as db:
            assert [item["marker_id"] for item in ambient_plan(db, "c1")] == [ids[1]]
            assert "Г1_Эмбиент - Лес.mp3" not in [f.canonical_filename for f in chapter_ambient_files(db, "c1")]
        assert TestSceneCarriesItsAmbient()._markers(factory)[ids[1]]["ambient"] is None
        from tests.test_ambient_session import _tracks_of
        assert "Г1_Эмбиент - Лес.mp3" not in [clip.name for track in _tracks_of(factory) for clip in track.clips]


class TestPlanRoute:
    def test_the_plan_counts_tracks_and_minutes(self, editor, factory):  # noqa: F811
        _scenes(factory)

        response = editor.get("/api/v2/chapters/c1/ambient/plan")

        assert response.status_code == 200
        assert response.json() == {"ok": True, "tracks": 3, "minutes": 9, "skipped": 0}

    def test_a_scene_without_a_place_is_skipped(self, editor, factory):  # noqa: F811
        with factory() as db:
            db.query(AsrJob).delete()
            db.query(AudioFile).delete()
            db.commit()
            _record(db, last_line_matched=False)
        _scenes(factory)

        assert editor.get("/api/v2/chapters/c1/ambient/plan").json() == {
            "ok": True, "tracks": 2, "minutes": 6, "skipped": 1}

    def test_a_generated_chapter_has_an_empty_plan(self, editor, factory):  # noqa: F811
        _scenes(factory)
        _go(factory)

        assert editor.get("/api/v2/chapters/c1/ambient/plan").json() == {
            "ok": True, "tracks": 0, "minutes": 0, "skipped": 0}

    def test_an_unknown_chapter_is_404(self, editor):
        response = editor.get("/api/v2/chapters/nope/ambient/plan")
        assert response.status_code == 404 and response.json()["error"] == "chapter_not_found"


class TestSceneCarriesItsAmbient:
    def _markers(self, factory):  # noqa: F811
        from app.services.sound_store import chapter_markers

        with factory() as db:
            payload = chapter_markers(db, "c1")
            db.commit()
        return {marker["id"]: marker for marker in payload["markers"]}

    def test_a_scene_without_a_track_has_none(self, factory):  # noqa: F811
        ids = _scenes(factory)
        markers = self._markers(factory)
        assert markers[ids[0]]["ambient"] is None

    def test_a_generated_scene_has_its_track(self, factory):  # noqa: F811
        ids = _scenes(factory)
        _go(factory)

        ambient = self._markers(factory)[ids[1]]["ambient"]

        assert ambient["status"] == "done" and ambient["prompt"] == "calm strings #2"
        assert ambient["file_name"] == "Г1_Эмбиент - Лес.mp3" and ambient["audio_file_id"]
        assert ambient["error"] == ""

    def test_a_failed_regeneration_keeps_the_track_to_listen(self, factory):  # noqa: F811
        ids = _scenes(factory)
        _go(factory)
        with factory() as db:
            done = db.query(AmbientTrack).filter(AmbientTrack.marker_id == ids[0]).one()
            db.add(AmbientTrack(book_id=BOOK, chapter_id="c1", marker_id=ids[0], prompt="new try", status="failed",
                                error="сбой", created_at=utcnow_naive() + timedelta(seconds=5)))
            db.commit()
            done_audio = done.audio_file_id

        ambient = self._markers(factory)[ids[0]]["ambient"]

        assert (ambient["status"], ambient["prompt"], ambient["error"]) == ("failed", "new try", "сбой")
        assert ambient["audio_file_id"] == done_audio

    def test_a_replaced_track_is_not_shown(self, factory):  # noqa: F811
        ids = _scenes(factory)
        _go(factory)
        with factory() as db:
            db.query(AmbientTrack).filter(AmbientTrack.marker_id == ids[0]).update({"status": "replaced"})
            db.commit()
        assert self._markers(factory)[ids[0]]["ambient"] is None

    def test_a_non_scene_marker_has_no_ambient_key(self, factory):  # noqa: F811
        with factory() as db:
            row = add_marker(db, chapter_id="c1", segment_id="c1:00002", kind="sound",
                             fields={"quote": "Они ушли", "description": "уход"})
            db.commit()
            marker_id = row.id
        assert "ambient" not in self._markers(factory)[marker_id]


def test_an_exhausted_month_refuses_ambient(factory, api, editor):
    from tests.spend_helpers import exhaust_month
    _scenes(factory) if "_scenes" in globals() else None
    exhaust_month(factory)
    response = editor.post("/api/v2/chapters/c1/ambient")
    assert response.status_code == 409 and response.json()["error"] == "over_limit" and api.calls == []
