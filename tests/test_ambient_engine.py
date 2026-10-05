"""Движок эмбиента по главе: подменённые Opus и ElevenLabs, книга в памяти.

Ни один тест не ходит в сеть: `ask` и `compose` подменены, уведомление — список.
"""
import json
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.v2.models  # noqa: F401
from app.db import Base
from app.models import AmbientTrack, AsrJob, AudioFile, BackgroundRun, ScriptChapter, SoundPlace
from app.services import audio_storage
from app.services.audio_uploads import TAKE
from app.services.elevenlabs_client import (
    BadKey,
    ElevenLabsError,
    ElevenLabsInterrupted,
    ElevenLabsUnavailable,
    QuotaExhausted,
)
from app.services.sound_store import add_marker
from app.time_utils import utcnow_naive
from tests.consilium_book import BOOK, build_book

MP3 = b"ID3fake-mp3-bytes"


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "audio_storage_path", str(tmp_path / "audio"))
    monkeypatch.setattr(settings, "local_reserve_gb", 0)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine)
    with make() as db:
        build_book(db)
        _record(db)
    return make


def _record(db, last_line_matched: bool = True):
    """Глава c1 записана: три роли, распознавание по всем репликам.

    Таймлайн по `_place_by_script`: абзац 0 — 0.0, абзац 1 — 3.7, абзац 2 — 5.55, конец — 6.55.
    """
    for role in ("Гамук", "Рассказчик", "Тупуг"):
        item = AudioFile(
            book_code="B1", original_filename=f"{role}.wav", stored_key=f"uploads/{role}.wav",
            canonical_filename=f"B1_Ch1_{role}.wav", mime_type="audio/wav", size_bytes=100,
            chapter="1", role=role, kind=TAKE, duration_seconds=12.0,
        )
        db.add(item)
        db.flush()
        if role == "Рассказчик":
            lines = [
                {"matched": True, "takes": [{"start": 2.0, "end": 3.5}]},
                {"matched": last_line_matched, "takes": [{"start": 6.0, "end": 7.0}]},
            ]
        else:
            lines = [{"matched": True, "takes": [{"start": 0.0, "end": 1.5}]}]
        db.add(AsrJob(audio_file_id=item.id, status="done", coverage=1.0,
                      alignment_json=json.dumps({"missing": [], "total": len(lines), "lines": lines})))
    db.commit()


def _place(db, name, description=""):
    place = SoundPlace(book_id=BOOK, name=name, description=description)
    db.add(place)
    db.flush()
    return place


def _scenes(factory, places=("Таверна", "Лес", "Таверна")):
    """Сцены на абзацах 0, 1, 2 главы c1 — по месту на сцену."""
    ids = []
    with factory() as db:
        for ordinal, name in enumerate(places):
            place = _place(db, name, f"описание {name}")
            row = add_marker(db, chapter_id="c1", segment_id=f"c1:{ordinal:05d}", kind="scene",
                             fields=dict(place_id=place.id, time_of_day="ночь", weather="дождь",
                                         ambience="гомон", mood="тревога", music_queries=["tavern"]))
            ids.append(row.id)
        db.commit()
    return ids


def _run(factory):
    with factory() as db:
        run = BackgroundRun(run_key="ambient:c1", job_kind="ambient", status="running", meta_json="{}")
        db.add(run)
        db.commit()
        return run.id


class Opus:
    """Подменённый Opus: уникальный summary на вызов, запоминает пользовательские промпты."""

    def __init__(self):
        self.users: list[str] = []

    def __call__(self, model, system, user, schema):
        self.users.append(user)
        n = len(self.users)
        return {"prompt": f"calm strings #{n}", "summary": f"summary-{n}"}


class Compose:
    """Подменённый ElevenLabs: `script` — что делать на каждом вызове (None — успех)."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls: list[tuple[str, int]] = []

    def __call__(self, prompt, seconds):
        self.calls.append((prompt, seconds))
        action = self.script.pop(0) if self.script else None
        if action is not None:
            raise action
        return MP3 + str(len(self.calls)).encode(), f"song-{len(self.calls)}"


def _go(factory, *, ask=None, compose=None, marker_id="", prompt_override="", notes=None):
    from app.services.ambient_engine import run_ambient

    run_id = _run(factory)
    notes = notes if notes is not None else []
    result = run_ambient(session_factory=factory, chapter_id="c1", run_id=run_id, marker_id=marker_id,
                         prompt_override=prompt_override, ask=ask or Opus(), compose=compose or Compose(),
                         notify=notes.append)
    return result, run_id


def _tracks(factory):
    with factory() as db:
        rows = db.query(AmbientTrack).order_by(AmbientTrack.created_at.asc(), AmbientTrack.id.asc()).all()
        db.expunge_all()
        return rows


class TestSceneSpans:
    def test_spans_follow_the_scene_marker_rules(self, factory):
        from app.services.chapter_delivery import scene_spans

        ids = _scenes(factory)
        with factory() as db:
            spans = scene_spans(db, "c1")

        assert set(spans) == set(ids)
        assert spans[ids[0]] == pytest.approx((0.0, 3.7))
        assert spans[ids[1]] == pytest.approx((3.7, 1.85))
        assert spans[ids[2]] == pytest.approx((5.55, 1.0))

    def test_a_scene_past_the_last_recorded_paragraph_has_no_span(self, factory):
        from app.services.chapter_delivery import scene_spans

        with factory() as db:
            db.query(AsrJob).delete()
            db.query(AudioFile).delete()
            db.commit()
            _record(db, last_line_matched=False)
        ids = _scenes(factory)
        with factory() as db:
            spans = scene_spans(db, "c1")

        assert ids[2] not in spans
        assert ids[0] in spans and ids[1] in spans

    def test_an_unknown_chapter_has_no_spans(self, factory):
        from app.services.chapter_delivery import scene_spans

        with factory() as db:
            assert scene_spans(db, "nope") == {}


def _llm_scenes(factory, ordinals=(0, 1, 2), places=("Таверна", "Лес", "Таверна"), run="r1"):
    """Сцены главы c1 от звуковой разметки (`source='llm'`), как их кладёт прогон.

    Второй вызов — это «Переразметить главу»: тот же прогон по тому же тексту.
    """
    from app.pipeline.sound_markers import Checked
    from app.models import SoundMarker
    from app.services.sound_store import _chapter_texts, apply_chapter

    scenes = [{"ordinal": ordinal,
               "place": {"id": "", "name": name, "description": f"описание {name}",
                         "ambience_queries": ["amb"]},
               "time_of_day": "ночь", "weather": "дождь", "ambience": "гомон", "mood": "тревога",
               "music_queries": ["tavern"]}
              for ordinal, name in zip(ordinals, places)]
    with factory() as db:
        texts, sha = _chapter_texts(db, "c1")
        apply_chapter(db, book_id=BOOK, chapter_id="c1", text_sha256=sha, checked=Checked(scenes=scenes),
                      run_id=run, texts=texts)
        db.commit()
        return [row.id for row in db.query(SoundMarker)
                .filter_by(chapter_id="c1", kind="scene", status="active")
                .order_by(SoundMarker.segment_id.asc())]


class TestRemarkKeepsPaidTracks:
    """Переразметка главы не должна отвязывать оплаченные треки её сцен."""

    def test_a_rerun_of_the_markup_leaves_nothing_to_generate(self, factory):
        from app.services.ambient_engine import ambient_plan

        ids = _llm_scenes(factory)
        _go(factory)
        with factory() as db:
            assert ambient_plan(db, "c1") == [], "все три сцены получили трек"

        assert _llm_scenes(factory, run="r2") == ids, "сцены узнаны, id сохранены"

        with factory() as db:
            assert ambient_plan(db, "c1") == [], "платить за те же сцены заново не надо"
            assert {t.marker_id for t in db.query(AmbientTrack)} == set(ids)


class TestAmbientPlan:
    def test_plan_lists_every_scene_with_length_and_name(self, factory):
        from app.services.ambient_engine import ambient_plan

        ids = _scenes(factory)
        with factory() as db:
            plan = ambient_plan(db, "c1")

        assert [item["marker_id"] for item in plan] == ids
        assert [item["file_name"] for item in plan] == [
            "Г1_Эмбиент - Таверна.mp3", "Г1_Эмбиент - Лес.mp3", "Г1_Эмбиент - Таверна 2.mp3"]
        assert all(item["seconds"] == 180 for item in plan), "короткие сцены зажаты снизу"
        assert plan[0]["scene"]["place"]["name"] == "Таверна"
        assert plan[0]["scene"]["mood"] == "тревога"


class TestRunGeneratesEveryScene:
    def test_all_scenes_get_a_file_an_audio_row_and_a_track(self, factory):
        ids = _scenes(factory)
        with factory() as db:
            db.get(ScriptChapter, "c1").session_archived_at = utcnow_naive()
            db.commit()
        notes = []
        compose = Compose()

        result, run_id = _go(factory, compose=compose, notes=notes)

        assert result["status"] == "done"
        assert (result["tracks_done"], result["tracks_total"], result["tracks_failed"]) == (3, 3, 0)
        assert [seconds for _prompt, seconds in compose.calls] == [180, 180, 180]
        tracks = _tracks(factory)
        assert [t.marker_id for t in tracks] == ids
        assert all(t.status == "done" and t.book_id == BOOK and t.chapter_id == "c1" for t in tracks)
        assert [t.summary for t in tracks] == ["summary-1", "summary-2", "summary-3"]
        assert [t.song_id for t in tracks] == ["song-1", "song-2", "song-3"]
        with factory() as db:
            files = {f.id: f for f in db.query(AudioFile).filter(AudioFile.kind == "ambient")}
            assert set(files) == {t.audio_file_id for t in tracks}
            first = files[tracks[0].audio_file_id]
            assert first.canonical_filename == "Г1_Эмбиент - Таверна.mp3"
            assert first.original_filename == first.canonical_filename
            assert first.stored_key.endswith("/Chapter01/Г1_Эмбиент - Таверна.mp3")
            assert (first.role, first.actor_name, first.chapter, first.location) == ("Эмбиент", "", "1", "local")
            assert first.mime_type == "audio/mpeg"
            assert first.size_bytes == len(MP3) + 1 and len(first.md5) == 32
            assert first.duration_seconds > 0
            assert audio_storage.read_file(first.stored_key) == MP3 + b"1"
            assert db.get(ScriptChapter, "c1").session_outdated_at is not None, "правило «б»"
            run = db.get(BackgroundRun, run_id)
            assert run.status == "done"
            assert json.loads(run.meta_json)["result"]["tracks_done"] == 3
        assert notes == ["Г1: эмбиент 3 из 3"]

    def test_a_second_run_generates_nothing(self, factory):
        _scenes(factory)
        _go(factory)
        compose = Compose()
        ask = Opus()

        result, _ = _go(factory, ask=ask, compose=compose)

        assert compose.calls == [] and ask.users == []
        assert result["tracks_total"] == 0 and result["status"] == "done"
        assert len(_tracks(factory)) == 3

    def test_previous_summaries_of_the_book_reach_the_prompt(self, factory):
        _scenes(factory)
        with factory() as db:
            db.add(AmbientTrack(book_id=BOOK, chapter_id="c2", marker_id="old", summary="harp · sparse · slow",
                                status="replaced"))
            db.add(AmbientTrack(book_id="other", chapter_id="x", marker_id="x", summary="alien book",
                                status="done"))
            db.add(AmbientTrack(book_id=BOOK, chapter_id="c2", marker_id="bad", summary="failed one",
                                status="failed"))
            db.commit()
        ask = Opus()

        _go(factory, ask=ask)

        assert "harp · sparse · slow" in ask.users[0]
        assert "alien book" not in ask.users[0] and "failed one" not in ask.users[0]
        assert "summary-1" in ask.users[1] and "summary-2" in ask.users[2], "свежие треки видны следующим"
        assert "Идём" in ask.users[0], "начало текста сцены"
        assert "Таверна" in ask.users[0]


class TestFailures:
    def test_quota_on_the_third_track_stops_and_keeps_the_first_two(self, factory):
        _scenes(factory)
        notes = []
        compose = Compose([None, None, QuotaExhausted("квота исчерпана: http 429", status=429)])

        result, run_id = _go(factory, compose=compose, notes=notes)

        assert result["status"] == "quota"
        assert result["tracks_done"] == 2
        assert len(compose.calls) == 3, "квоту не повторяют"
        assert [t.status for t in _tracks(factory)] == ["done", "done"]
        with factory() as db:
            assert db.get(BackgroundRun, run_id).status == "done", "квота — не сбой прогона"
        assert notes == ["Г1: эмбиент 2 из 3, квота исчерпана"]

    def test_two_failures_mark_the_track_failed_and_the_rest_go_on(self, factory):
        ids = _scenes(factory)
        notes = []
        compose = Compose([None, ElevenLabsUnavailable("http 503"), ElevenLabsUnavailable("http 503")])

        result, _ = _go(factory, compose=compose, notes=notes)

        assert result["status"] == "done"
        assert (result["tracks_done"], result["tracks_failed"]) == (2, 1)
        assert len(compose.calls) == 4
        tracks = {t.marker_id: t for t in _tracks(factory)}
        assert tracks[ids[1]].status == "failed" and "503" in tracks[ids[1]].error
        assert tracks[ids[1]].audio_file_id is None
        assert tracks[ids[0]].status == tracks[ids[2]].status == "done"
        assert notes == ["Г1: эмбиент 2 из 3, ошибок 1"]

    def test_a_failed_scene_is_retried_by_the_next_run(self, factory):
        ids = _scenes(factory)
        _go(factory, compose=Compose([None, ElevenLabsUnavailable("x"), ElevenLabsUnavailable("x")]))
        compose = Compose()

        result, _ = _go(factory, compose=compose)

        assert result["tracks_done"] == 1 and len(compose.calls) == 1
        done = [t.marker_id for t in _tracks(factory) if t.status == "done"]
        assert sorted(done) == sorted(ids)

    def test_one_ask_failure_is_retried(self, factory):
        _scenes(factory, places=("Таверна",))
        calls = []

        def flaky(model, system, user, schema):
            calls.append(user)
            if len(calls) == 1:
                raise RuntimeError("routerai hiccup")
            return {"prompt": "p", "summary": "s"}

        result, _ = _go(factory, ask=flaky)

        assert result["tracks_done"] == 1 and len(calls) == 2

    def test_a_scene_without_a_span_is_skipped(self, factory):
        with factory() as db:
            db.query(AsrJob).delete()
            db.query(AudioFile).delete()
            db.commit()
            _record(db, last_line_matched=False)
        _scenes(factory)
        compose = Compose()

        result, _ = _go(factory, compose=compose)

        assert (result["tracks_done"], result["tracks_skipped"]) == (2, 1)
        assert len(compose.calls) == 2

    def test_an_unexpected_error_fails_the_run(self, factory, monkeypatch):
        from app.services import ambient_engine

        _scenes(factory)
        monkeypatch.setattr(ambient_engine, "_previous_summaries",
                            lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
        notes = []

        with pytest.raises(KeyError):
            _go(factory, notes=notes)

        with factory() as db:
            run = db.query(BackgroundRun).one()
            assert run.status == "failed"
        assert notes and "упал" in notes[0]


class TestRegeneration:
    def test_one_scene_regenerates_and_the_old_track_is_replaced(self, factory):
        ids = _scenes(factory)
        _go(factory)
        old = next(t for t in _tracks(factory) if t.marker_id == ids[0])
        compose = Compose()

        result, _ = _go(factory, marker_id=ids[0], prompt_override="solo cello, sparse", compose=compose)

        assert result["tracks_done"] == 1
        assert compose.calls == [("solo cello, sparse", 180)], "правленый промпт идёт как есть"
        rows = [t for t in _tracks(factory) if t.marker_id == ids[0]]
        assert [t.status for t in rows] == ["replaced", "done"]
        new = rows[1]
        assert new.prompt == "solo cello, sparse"
        with factory() as db:
            old_file = db.get(AudioFile, old.audio_file_id)
            new_file = db.get(AudioFile, new.audio_file_id)
            assert audio_storage.file_exists(old_file.stored_key), "прежний файл остаётся"
            assert new_file.stored_key != old_file.stored_key
            assert new_file.canonical_filename == "Г1_Эмбиент - Таверна (2).mp3"
            assert audio_storage.read_file(old_file.stored_key) == MP3 + b"1"

    def test_regeneration_without_override_asks_opus(self, factory):
        ids = _scenes(factory)
        _go(factory)
        ask = Opus()

        _go(factory, marker_id=ids[1], ask=ask)

        assert len(ask.users) == 1
        assert [t.status for t in _tracks(factory) if t.marker_id == ids[1]] == ["replaced", "done"]


class TestTheKeyStaysSecret:
    def test_the_key_never_reaches_logs_or_the_result(self, factory, monkeypatch, caplog):
        from app.config import settings
        from app.services.elevenlabs_client import compose_music

        secret = "sk-very-secret-key-123"
        monkeypatch.setattr(settings, "elevenlabs_api_key", secret)
        _scenes(factory, places=("Таверна", "Лес"))
        answers = [(503, {}, b"down"), (503, {}, b"down"), (200, {"song-id": "s1"}, MP3)]

        def transport(url, headers, body, timeout):
            assert headers["xi-api-key"] == secret
            return answers.pop(0)

        def compose(prompt, seconds):
            return compose_music(prompt, seconds, transport=transport)

        with caplog.at_level(logging.DEBUG):
            result, run_id = _go(factory, compose=compose)

        assert result["tracks_failed"] == 1 and result["tracks_done"] == 1
        with factory() as db:
            meta = db.get(BackgroundRun, run_id).meta_json
        errors = " ".join(t.error for t in _tracks(factory))
        for text in (json.dumps(result, ensure_ascii=False), meta, errors, caplog.text):
            assert secret not in text


class TestQueue:
    def test_enqueue_goes_to_the_consilium_lane_with_a_chapter_key(self, monkeypatch):
        from app.services import ambient_engine
        from app.workers import launcher

        seen = {}
        monkeypatch.setattr(launcher, "enqueue_tracked_task", lambda **kw: seen.update(kw) or "job-1")

        assert ambient_engine.enqueue_ambient("c1", marker_id="m1", prompt_override="p") == "job-1"
        assert seen["queue_name"] == "consilium"
        assert seen["func_ref"] == "app.worker_tasks.perform_ambient_task"
        assert (seen["run_key"], seen["job_kind"]) == ("ambient:c1", "ambient")
        assert (seen["chapter_id"], seen["marker_id"], seen["prompt_override"]) == ("c1", "m1", "p")

    def test_the_worker_skips_a_run_that_is_no_longer_queued(self, factory, monkeypatch):
        import app.worker_tasks as worker_tasks
        from app.services import ambient_engine

        with factory() as db:
            run = BackgroundRun(run_key="ambient:c1", job_kind="ambient", status="stopped", meta_json="{}")
            db.add(run)
            db.commit()
            run_id = run.id
        monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
        monkeypatch.setattr(ambient_engine, "run_ambient", lambda **kw: pytest.fail("не должен запускаться"))

        assert worker_tasks.perform_ambient_task("c1", run_id=run_id) is None


class TestDiskBeforeMoney:
    """I1: место на диске проверяется до оплаты трека; сбой записи после оплаты — `failed`
    с внятной причиной, прогон не падает."""

    def test_no_room_before_compose_stops_without_paying(self, factory, monkeypatch):
        from app.services import audio_mirror

        _scenes(factory)

        def full(size_hint, *, free, reserve_bytes):
            raise audio_mirror.LocalDiskFull("local_disk_full")

        monkeypatch.setattr(audio_mirror, "ensure_room", full)
        compose = Compose()
        notes = []

        result, run_id = _go(factory, compose=compose, notes=notes)

        assert compose.calls == [], "без места не платим"
        assert result["status"] == "disk_full" and result["tracks_done"] == 0
        with factory() as db:
            assert db.get(BackgroundRun, run_id).status == "done"
        assert "не хватило места на диске" in notes[0]

    def test_the_preflight_asks_for_the_upper_bound_of_the_track(self, factory, monkeypatch):
        from app.services import audio_mirror

        _scenes(factory, places=("Таверна",))
        hints = []
        monkeypatch.setattr(audio_mirror, "ensure_room",
                            lambda size_hint, *, free, reserve_bytes: hints.append(size_hint))

        _go(factory)

        assert hints[0] == 180 * 192000 // 8, "до оплаты — верхняя граница mp3 192 кбит/с"
        assert hints[1] == len(MP3) + 1, "после — настоящий размер"

    def test_a_store_failure_after_paying_marks_the_track_failed_and_goes_on(self, factory, monkeypatch):
        from app.services import audio_mirror

        ids = _scenes(factory)
        calls = []

        def second_call_full(size_hint, *, free, reserve_bytes):
            calls.append(size_hint)
            if len(calls) == 2:  # проверка после оплаты первого трека
                raise audio_mirror.LocalDiskFull("local_disk_full")

        monkeypatch.setattr(audio_mirror, "ensure_room", second_call_full)

        result, run_id = _go(factory)

        assert (result["tracks_done"], result["tracks_failed"]) == (2, 1)
        tracks = {t.marker_id: t for t in _tracks(factory)}
        assert tracks[ids[0]].status == "failed"
        assert "не хватило места на диске — трек оплачен, но не сохранён" in tracks[ids[0]].error
        assert tracks[ids[0]].song_id == "song-1", "оплаченный трек можно найти по song_id"
        with factory() as db:
            assert db.get(BackgroundRun, run_id).status == "done"


class TestSceneFailuresStayLocal:
    def test_a_malformed_marker_does_not_break_the_spans(self, factory):
        from app.models import SoundMarker
        from app.services.chapter_delivery import scene_spans

        ids = _scenes(factory)
        with factory() as db:
            db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:bad", kind="scene",
                               payload={}, text_sha256="x"))
            db.commit()
            spans = scene_spans(db, "c1")

        assert set(spans) == set(ids)

    def test_a_malformed_marker_does_not_break_the_run(self, factory):
        from app.models import SoundMarker

        _scenes(factory)
        with factory() as db:
            db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:bad", kind="scene",
                               payload={}, text_sha256="x"))
            db.commit()

        result, _ = _go(factory)

        assert result["tracks_done"] == 3

    def test_a_failed_regeneration_keeps_the_old_track(self, factory):
        ids = _scenes(factory)
        _go(factory)
        old = next(t for t in _tracks(factory) if t.marker_id == ids[0])

        result, _ = _go(factory, marker_id=ids[0],
                        compose=Compose([ElevenLabsUnavailable("x"), ElevenLabsUnavailable("x")]))

        assert result["tracks_failed"] == 1
        rows = [t for t in _tracks(factory) if t.marker_id == ids[0]]
        assert [t.status for t in rows] == ["done", "failed"]
        assert rows[0].id == old.id
        with factory() as db:
            assert audio_storage.file_exists(db.get(AudioFile, old.audio_file_id).stored_key)

    def test_regenerating_an_unknown_marker_says_not_found(self, factory):
        _scenes(factory)
        notes = []
        compose = Compose()

        result, _ = _go(factory, marker_id="nope", compose=compose, notes=notes)

        assert result["status"] == "not_found"
        assert compose.calls == []
        assert "сцена не найдена" in notes[0]

    def test_an_orphan_file_on_disk_is_logged_and_not_overwritten(self, factory, caplog):
        from app.config import settings  # noqa: F401

        _scenes(factory, places=("Таверна",))
        orphan = "K/Chapter01/Г1_Эмбиент - Таверна.mp3"
        audio_storage.write_file(orphan, b"orphan")

        with caplog.at_level(logging.WARNING, logger="app.services.ambient_engine"):
            _go(factory)

        assert audio_storage.read_file(orphan) == b"orphan"
        assert "Г1_Эмбиент - Таверна.mp3" in caplog.text and "без строки" in caplog.text
        with factory() as db:
            item = db.query(AudioFile).filter(AudioFile.kind == "ambient").one()
            assert item.canonical_filename == "Г1_Эмбиент - Таверна (2).mp3"


class TestNoDoublePayment:
    """Повтор — только когда запрос точно не стоил денег: не ушёл или получил 5xx."""

    def test_an_interrupted_answer_is_not_retried(self, factory):
        ids = _scenes(factory)
        compose = Compose([ElevenLabsInterrupted("ответ ElevenLabs оборвался: TimeoutError")])

        result, _ = _go(factory, compose=compose)

        assert len(compose.calls) == 3, "оборванный ответ не повторяют — трек мог быть оплачен"
        assert (result["tracks_done"], result["tracks_failed"]) == (2, 1)
        failed = next(t for t in _tracks(factory) if t.marker_id == ids[0])
        assert failed.status == "failed" and failed.audio_file_id is None
        assert failed.error == ("ответ ElevenLabs оборвался — трек мог быть оплачен; "
                                "проверьте историю в ElevenLabs")

    def test_a_request_that_never_left_is_retried(self, factory):
        _scenes(factory, places=("Таверна",))
        compose = Compose([ElevenLabsUnavailable("сеть недоступна: gaierror", sent=False)])

        result, _ = _go(factory, compose=compose)

        assert len(compose.calls) == 2 and result["tracks_done"] == 1

    def test_another_4xx_fails_the_scene_without_retry(self, factory):
        _scenes(factory, places=("Таверна", "Лес"))
        compose = Compose([ElevenLabsError("http 422: bad prompt", status=422)])

        result, _ = _go(factory, compose=compose)

        assert len(compose.calls) == 2, "422 не повторяют, следующая сцена идёт"
        assert (result["tracks_done"], result["tracks_failed"]) == (1, 1)

    def test_an_empty_answer_is_never_a_done_track(self, factory):
        from app.services.elevenlabs_client import compose_music

        _scenes(factory, places=("Таверна",))

        def compose(prompt, seconds):
            return compose_music(prompt, seconds, api_key="k",
                                 transport=lambda url, headers, body, timeout: (200, {}, b""))

        result, _ = _go(factory, compose=compose)

        assert (result["tracks_done"], result["tracks_failed"]) == (0, 1)
        assert [t.status for t in _tracks(factory)] == ["failed"]
        assert "пустой ответ" in _tracks(factory)[0].error


class TestBadKey:
    def test_a_rejected_key_stops_the_run(self, factory):
        _scenes(factory)
        notes = []
        compose = Compose([None, BadKey("ключ отклонён: http 401", status=401)])

        result, run_id = _go(factory, compose=compose, notes=notes)

        assert result["status"] == "bad_key"
        assert len(compose.calls) == 2, "с отклонённым ключом дальше не идут"
        assert result["tracks_done"] == 1
        with factory() as db:
            run = db.get(BackgroundRun, run_id)
            assert run.status == "done" and run.error_message == "Ключ ElevenLabs отклонён"
        assert notes == ["Г1: эмбиент 1 из 3, ключ ElevenLabs отклонён"]


class TestTheCardStopsWaiting:
    """I3: перегенерация одной сцены, кончившаяся без трека, пишет сцене `failed` с причиной."""

    def _scene_view(self, factory, marker_id):
        from app.services.sound_store import scene_ambient

        with factory() as db:
            return scene_ambient(db, "c1").get(marker_id)

    def test_quota_on_one_scene_leaves_a_failed_row(self, factory):
        ids = _scenes(factory)
        _go(factory)
        before = self._scene_view(factory, ids[0])

        result, _ = _go(factory, marker_id=ids[0], compose=Compose([QuotaExhausted("402", status=402)]))

        assert result["status"] == "quota"
        rows = [t for t in _tracks(factory) if t.marker_id == ids[0]]
        assert [t.status for t in rows] == ["done", "failed"]
        assert rows[1].error == "квота исчерпана" and rows[1].audio_file_id is None
        after = self._scene_view(factory, ids[0])
        assert after != before and after["status"] == "failed"
        assert after["audio_file_id"] == before["audio_file_id"], "прежний трек слушается"

    def test_disk_full_on_one_scene_leaves_a_failed_row(self, factory, monkeypatch):
        from app.services import audio_mirror

        ids = _scenes(factory)

        def full(size_hint, *, free, reserve_bytes):
            raise audio_mirror.LocalDiskFull("local_disk_full")

        monkeypatch.setattr(audio_mirror, "ensure_room", full)

        result, _ = _go(factory, marker_id=ids[1])

        assert result["status"] == "disk_full"
        rows = _tracks(factory)
        assert [(t.marker_id, t.status, t.error) for t in rows] == [(ids[1], "failed", "не хватило места на диске")]

    def test_a_bad_key_on_one_scene_leaves_a_failed_row(self, factory):
        ids = _scenes(factory)

        _go(factory, marker_id=ids[2], compose=Compose([BadKey("401", status=401)]))

        assert [(t.status, t.error) for t in _tracks(factory)] == [("failed", "Ключ ElevenLabs отклонён")]

    def test_a_missing_scene_leaves_a_failed_row(self, factory):
        _scenes(factory)

        _go(factory, marker_id="gone")

        assert [(t.marker_id, t.status) for t in _tracks(factory)] == [("gone", "failed")]

    def test_an_identical_failure_still_changes_the_snapshot(self, factory):
        ids = _scenes(factory)
        _go(factory, marker_id=ids[0], compose=Compose([QuotaExhausted("402")]))
        first = self._scene_view(factory, ids[0])

        _go(factory, marker_id=ids[0], compose=Compose([QuotaExhausted("402")]))
        second = self._scene_view(factory, ids[0])

        assert (first["status"], first["error"]) == (second["status"], second["error"])
        assert first["id"] != second["id"] and second["updated_at"]
        assert first != second

    def test_a_chapter_run_writes_no_extra_rows_on_quota(self, factory):
        _scenes(factory)

        _go(factory, compose=Compose([QuotaExhausted("402")]))

        assert _tracks(factory) == [], "по всей главе ждать некому — строки не нужны"

    def test_failed_rows_never_count_as_done(self, factory):
        from app.services.ambient_engine import ambient_plan

        ids = _scenes(factory)
        _go(factory, marker_id=ids[0], compose=Compose([QuotaExhausted("402")]))

        with factory() as db:
            assert {item["marker_id"] for item in ambient_plan(db, "c1")} == set(ids)


class TestStop:
    def test_a_stop_between_tracks_ends_the_run(self, factory):
        from app.services.consilium_engine import request_stop

        _scenes(factory)
        notes = []

        class StopAfterFirst(Compose):
            def __call__(self, prompt, seconds):
                out = super().__call__(prompt, seconds)
                with factory() as db:
                    assert request_stop(db, "c1", kind="ambient")
                    db.commit()
                return out

        compose = StopAfterFirst()
        result, run_id = _go(factory, compose=compose, notes=notes)

        assert result["status"] == "stopped" and result["tracks_done"] == 1
        assert len(compose.calls) == 1, "текущий трек доделан, следующий не начат"
        with factory() as db:
            assert db.get(BackgroundRun, run_id).status == "stopped"
        assert notes == ["Г1: эмбиент 1 из 3, остановлен вручную"]


def test_a_store_failure_is_logged_with_the_traceback(factory, monkeypatch, caplog):
    from app.services import audio_mirror

    _scenes(factory, places=("Таверна",))
    calls = []

    def second_call_full(size_hint, *, free, reserve_bytes):
        calls.append(size_hint)
        if len(calls) == 2:
            raise audio_mirror.LocalDiskFull("local_disk_full")

    monkeypatch.setattr(audio_mirror, "ensure_room", second_call_full)

    with caplog.at_level(logging.ERROR, logger="app.services.ambient_engine"):
        _go(factory)

    records = [r for r in caplog.records if "не сохранён" in r.getMessage()]
    assert records and records[0].exc_info is not None


def test_the_ambient_text_model_comes_from_its_step(factory, monkeypatch):
    from app.services import step_models
    monkeypatch.setattr(step_models, "step_model",
                        lambda key: {"ambient_text": ("openrouter", "vendor/ambient")}.get(key, ("elevenlabs", "music_v2")))
    seen = []

    class Recorder(Opus):
        def __call__(self, model, system, user, schema):
            seen.append(model)
            return super().__call__(model, system, user, schema)
    _scenes(factory)
    _go(factory, ask=Recorder())
    assert seen and set(seen) == {"vendor/ambient"}


def test_ambient_without_the_text_key_stops_before_any_call(factory, monkeypatch):
    from app.services import provider_keys
    monkeypatch.setattr(provider_keys, "provider_key", lambda name: "" if name == "routerai" else "xi")
    monkeypatch.setattr("app.pipeline.llm_client.call_chat",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("платный вызов без ключа")))
    from app.services.ambient_engine import run_ambient
    _scenes(factory)
    run_id = _run(factory)
    result = run_ambient(session_factory=factory, chapter_id="c1", run_id=run_id, compose=Compose(),
                         notify=lambda _t: None)
    assert result["status"] == "stopped" and "RouterAI" in result["reason"]


def test_every_track_of_a_run_uses_the_audio_model_from_its_start(factory, monkeypatch):
    from app.services import ambient_engine, step_models
    models = iter(["first", "second", "third", "fourth"])
    monkeypatch.setattr(step_models, "step_model",
                        lambda key: ("elevenlabs", next(models)) if key == "ambient_audio" else ("routerai", "m"))
    used = []

    def compose_music(prompt, seconds, *, model_id=None):
        used.append(model_id)
        return b"ID3" + b"0" * 2000, "song"
    monkeypatch.setattr(ambient_engine, "compose_music", compose_music)
    from app.services import provider_keys
    monkeypatch.setattr(provider_keys, "provider_key", lambda name: "k")  # настоящий путь звука требует ключ
    _scenes(factory)
    run_id = _run(factory)
    ambient_engine.run_ambient(session_factory=factory, chapter_id="c1", run_id=run_id, ask=Opus(), notify=lambda _t: None)
    assert len(used) >= 2 and set(used) == {"first"}


def test_ambient_without_the_audio_key_stops_before_the_paid_text(factory, monkeypatch):
    from app.services import ambient_engine, provider_keys
    monkeypatch.setattr(provider_keys, "provider_key", lambda name: "" if name == "elevenlabs" else "k")
    monkeypatch.setattr("app.pipeline.llm_client.call_chat",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("оплачен текст без ключа звука")))
    _scenes(factory)
    result = ambient_engine.run_ambient(session_factory=factory, chapter_id="c1", run_id=_run(factory),
                                        notify=lambda _t: None)
    assert result["status"] == "stopped" and "ElevenLabs" in result["reason"]
