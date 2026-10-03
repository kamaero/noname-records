"""Маркеры звукорежиссёра на таймлайне проекта Audition.

Сцена/переход/звук уже лежат в `sound_markers`, а у главы уже есть таймлайн — этот
файл проверяет только их сведение: где встаёт маркер абзаца, который не записан,
номер сцены и то, что пересборка не меняет GUID (без этого `session_fingerprint`
видел бы «перемену» на каждом фоновом круге).
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
import app.v2.models  # noqa: F401
from app.db import Base
from app.models import AudioFile, AsrJob, ScriptChapter, SoundMarker
from app.services.audio_uploads import TAKE
from app.services.chapter_delivery import book_recording_status, session_markers
from app.services.sound_store import add_marker, touch_sessions
from app.time_utils import utcnow_naive
from tests.consilium_book import BOOK, build_book


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        build_book(session)
        yield session


def _scene(db, chapter_id, ordinal, **fields):
    defaults = dict(time_of_day="ночь", weather="дождь", ambience="гомон", mood="тревога",
                    music_queries=["tavern"])
    defaults.update(fields)
    row = add_marker(db, chapter_id=chapter_id, segment_id=f"{chapter_id}:{ordinal:05d}", kind="scene",
                     fields=defaults)
    db.commit()
    return row


def _sound(db, chapter_id, ordinal, quote, **fields):
    defaults = dict(description="стук", queries=["knock"])
    defaults.update(fields)
    defaults["quote"] = quote
    row = add_marker(db, chapter_id=chapter_id, segment_id=f"{chapter_id}:{ordinal:05d}", kind="sound",
                     fields=defaults)
    db.commit()
    return row


def _transition(db, chapter_id, ordinal, what):
    row = add_marker(db, chapter_id=chapter_id, segment_id=f"{chapter_id}:{ordinal:05d}", kind="transition",
                     fields={"what": what})
    db.commit()
    return row


class TestSceneRunsToTheNextScene:
    def test_two_scenes_split_the_timeline_between_them(self, db):
        _scene(db, "c1", 0)
        _scene(db, "c1", 2)
        starts = {"c1:00000": 0.0, "c1:00001": 10.0, "c1:00002": 40.0}

        markers, skipped = session_markers(db, "c1", starts, end=90.0)

        assert skipped == 0
        assert [m.name.split(" · ")[0] for m in markers] == ["СЦЕНА 1", "СЦЕНА 2"]
        assert (markers[0].start, markers[0].duration) == (0.0, 40.0)
        assert (markers[1].start, markers[1].duration) == (40.0, 50.0), "последняя сцена — до конца главы"


class TestAnUnrecordedParagraphShiftsTheMarker:
    def test_a_sound_on_an_unrecorded_paragraph_moves_to_the_next_recorded_one(self, db):
        _sound(db, "c1", 1, "Куда")
        starts = {"c1:00000": 0.0, "c1:00002": 12.0}  # абзац 1 не записан

        markers, skipped = session_markers(db, "c1", starts, end=20.0)

        assert skipped == 0
        assert len(markers) == 1
        assert markers[0].start == 12.0
        assert "абзац не записан, маркер сдвинут" in markers[0].comment


class TestAMarkerPastTheLastRecordedParagraphIsDropped:
    def test_nothing_recorded_after_the_marker_means_it_is_skipped(self, db):
        _sound(db, "c1", 2, "ушли")
        starts = {"c1:00000": 0.0}  # дальше абзаца 0 ничего не записано

        markers, skipped = session_markers(db, "c1", starts, end=5.0)

        assert markers == []
        assert skipped == 1


class TestZeroLengthScenesDoNotBecomePoints:
    """Ни одна сцена не должна тихо стать точкой: либо у неё настоящий отрезок (пусть
    даже пересекающийся с соседкой — обе сдвинуты на один и тот же незаписанный абзац),
    либо её вовсе не ставят и считают пропущенной."""

    def test_two_scenes_collapsed_onto_the_same_paragraph_both_get_a_real_span(self, db):
        # Абзацы 0 и 1 не записаны — обе сцены сдвигаются на начало абзаца 2.
        _scene(db, "c1", 0)
        _scene(db, "c1", 1)
        starts = {"c1:00002": 40.0}

        markers, skipped = session_markers(db, "c1", starts, end=50.0)

        assert skipped == 0
        assert len(markers) == 2
        assert all(m.start == 40.0 for m in markers)
        assert all(m.duration == pytest.approx(10.0) for m in markers), "до конца главы, а не 0"

    def test_a_scene_with_no_positive_span_left_is_dropped_and_counted(self, db):
        # Абзац 2 — последний записанный, и он же конец главы: сцене больше некуда тянуться.
        _scene(db, "c1", 2)
        starts = {"c1:00000": 0.0, "c1:00002": 20.0}

        markers, skipped = session_markers(db, "c1", starts, end=20.0)

        assert markers == []
        assert skipped == 1


class TestTheSceneNameSkipsEmptyParts:
    def test_missing_time_of_day_and_weather_leave_no_dangling_separators(self, db):
        _scene(db, "c1", 0, time_of_day="", weather="")
        starts = {"c1:00000": 0.0}

        markers, _ = session_markers(db, "c1", starts, end=5.0)

        assert markers[0].name == "СЦЕНА 1 · без места"


class TestDismissedAndLostStayOffTheTimeline:
    def test_a_dismissed_marker_does_not_show_up(self, db):
        from app.services.sound_store import dismiss_marker

        row = _sound(db, "c1", 0, "Идём")
        dismiss_marker(db, row.id)
        db.commit()
        starts = {"c1:00000": 0.0}

        markers, skipped = session_markers(db, "c1", starts, end=5.0)

        assert markers == []
        assert skipped == 0

    def test_a_lost_marker_does_not_show_up(self, db):
        db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:00000", kind="scene",
                           status="lost", payload={}, text_sha256="x"))
        db.commit()
        starts = {"c1:00000": 0.0}

        markers, skipped = session_markers(db, "c1", starts, end=5.0)

        assert markers == []
        assert skipped == 0


class TestTheGuidIsStable:
    def test_two_builds_of_the_same_marker_get_the_same_guid(self, db):
        _scene(db, "c1", 0)
        starts = {"c1:00000": 0.0}

        first, _ = session_markers(db, "c1", starts, end=5.0)
        second, _ = session_markers(db, "c1", starts, end=5.0)

        assert first[0].guid == second[0].guid
        assert first[0].guid.startswith("xmp:id:")


class TestSoundAndTransitionMarkers:
    def test_a_sound_carries_its_quote_and_queries_in_the_comment(self, db):
        _sound(db, "c1", 2, "ушли", description="уходят шаги", queries=["footsteps leaving"])
        starts = {"c1:00002": 7.0}

        markers, _ = session_markers(db, "c1", starts, end=8.0)

        assert markers[0].name == "● уходят шаги"
        assert markers[0].comment == "«ушли» · 🔎 footsteps leaving"
        assert markers[0].duration == 0.0

    def test_a_transition_is_named_after_what(self, db):
        _transition(db, "c1", 1, "флешбэк")
        starts = {"c1:00001": 3.0}

        markers, _ = session_markers(db, "c1", starts, end=4.0)

        assert markers[0].name == "◆ флешбэк"


class TestBuildChapterSessionCarriesTheMarkers:
    """Интеграционный конец в конец: реальная раскладка сценария плюс реальный маркер
    должны выйти в `.sesx` как `xmpDM:name`, а без маркеров — без `xmpMetadata` вовсе."""

    def _record(self, db):
        chapter_id = "c1"
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
                    {"matched": True, "takes": [{"start": 6.0, "end": 7.0}]},
                ]
            else:
                lines = [{"matched": True, "takes": [{"start": 0.0, "end": 1.5}]}]
            db.add(AsrJob(audio_file_id=item.id, status="done", coverage=1.0,
                          alignment_json=json.dumps({"missing": [], "total": len(lines), "lines": lines})))
        db.commit()
        return chapter_id

    def test_a_session_with_a_marker_names_the_scene(self, db):
        from app.services.chapter_delivery import build_chapter_session

        chapter_id = self._record(db)

        without_markers = build_chapter_session(db, chapter_id)
        assert without_markers is not None
        assert "xmpMetadata" not in without_markers

        _scene(db, chapter_id, 0)
        with_markers = build_chapter_session(db, chapter_id)

        assert with_markers is not None
        assert "<xmpDM:name>СЦЕНА 1" in with_markers

    def test_session_fingerprint_is_stable_across_rebuilds(self, db):
        from app.services.chapter_delivery import build_chapter_session, session_fingerprint

        chapter_id = self._record(db)
        _scene(db, chapter_id, 0)

        first = build_chapter_session(db, chapter_id)
        second = build_chapter_session(db, chapter_id)

        assert session_fingerprint(first) == session_fingerprint(second)


class TestABadMarkerDoesNotBlockTheSession:
    """F4: один битый маркер (например, испорченный `segment_id`) не должен срывать
    сборку сессии целиком — дубли записаны, монтажёру нужна хотя бы раскладка."""

    def test_a_malformed_segment_id_falls_back_to_no_markers(self, db, caplog):
        import logging

        from app.services.chapter_delivery import build_chapter_session_with_stats

        db.add(SoundMarker(book_id=BOOK, chapter_id="c1", segment_id="c1:bad", kind="transition",
                           payload={"what": "флешбэк"}, text_sha256="x"))
        db.commit()

        with caplog.at_level(logging.ERROR, logger="app.services.chapter_delivery"):
            xml, skipped = build_chapter_session_with_stats(db, "c1")

        assert xml is not None
        assert skipped == 0
        assert "session_markers сорвались" in caplog.text


class TestTouchSessionsOutdatesArchivedChapters:
    """Правка маркеров/мест ставит `session_outdated_at` архивной сессии — кроме сданных
    глав (решение владельца «б»): правка разметки после сдачи не должна дёргать монтаж."""

    def test_an_archived_chapter_gets_outdated(self, db):
        chapter = db.get(ScriptChapter, "c1")
        chapter.session_archived_at = utcnow_naive()
        db.commit()

        touch_sessions(db, ["c1"])
        db.commit()

        assert db.get(ScriptChapter, "c1").session_outdated_at is not None

    def test_a_delivered_chapter_is_left_alone(self, db):
        chapter = db.get(ScriptChapter, "c1")
        chapter.session_archived_at = utcnow_naive()
        chapter.delivered_at = utcnow_naive()
        db.commit()

        touch_sessions(db, ["c1"])
        db.commit()

        assert db.get(ScriptChapter, "c1").session_outdated_at is None

    def test_a_chapter_without_an_archive_is_a_no_op(self, db):
        touch_sessions(db, ["c1"])
        db.commit()

        assert db.get(ScriptChapter, "c1").session_outdated_at is None

    def test_touches_only_the_listed_chapters(self, db):
        chapter1 = db.get(ScriptChapter, "c1")
        chapter1.session_archived_at = utcnow_naive()
        chapter2 = db.get(ScriptChapter, "c2")
        chapter2.session_archived_at = utcnow_naive()
        db.commit()

        touch_sessions(db, ["c1"])
        db.commit()

        assert db.get(ScriptChapter, "c1").session_outdated_at is not None
        assert db.get(ScriptChapter, "c2").session_outdated_at is None

    def test_an_empty_list_does_nothing(self, db):
        chapter = db.get(ScriptChapter, "c1")
        chapter.session_archived_at = utcnow_naive()
        db.commit()

        touch_sessions(db, [])
        db.commit()

        assert db.get(ScriptChapter, "c1").session_outdated_at is None


class TestBookRecordingStatusCarriesTheSkippedMarkerCount:
    """`session_markers_skipped` — то, что записала архивация; таблица ASR его только
    показывает, не считает заново."""

    def test_the_stored_counter_passes_through(self, db):
        db.get(ScriptChapter, "c1").session_markers_skipped = 3
        db.commit()

        row = next(r for r in book_recording_status(db, BOOK)["chapters"] if r["chapter_id"] == "c1")

        assert row["session_markers_skipped"] == 3

    def test_a_chapter_that_never_archived_shows_zero(self, db):
        row = next(r for r in book_recording_status(db, BOOK)["chapters"] if r["chapter_id"] == "c1")

        assert row["session_markers_skipped"] == 0
