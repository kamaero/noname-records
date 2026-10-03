"""Эмбиент в проекте главы и в стороне от записи.

Треки ложатся на «Ambient 1/2» поочерёдно от начала своих сцен; глава без готового трека
собирается в прежний проект. Подсчёты записи, опись и архив главы, карта персонажей,
недавние файлы диктора и DAW строки `kind='ambient'` не видят.
"""
import json
import zipfile

import pytest

from app.models import AmbientTrack, AsrJob, AudioFile, ScriptChapter
from app.services.sound_store import dismiss_marker
from tests.consilium_book import BOOK
from tests.test_ambient_engine import _go, _scenes, factory  # noqa: F401 — фикстура
from tests.test_chapter_session_archive import roots  # noqa: F401 — фикстура


def _ambient_files(db):
    return {item.id: item for item in db.query(AudioFile).filter(AudioFile.kind == "ambient")}


def _tracks_of(factory, relative=False):
    from app.services.chapter_delivery import _script_layout, ambient_session_tracks

    with factory() as db:
        _status, _book, _by_role, _placed, starts, end = _script_layout(db, "c1")
        return ambient_session_tracks(db, "c1", starts, end, relative=relative)


class TestAmbientTracksInTheSession:
    def test_scenes_alternate_between_two_tracks_from_the_scene_start(self, factory):
        _scenes(factory)
        _go(factory)

        tracks = _tracks_of(factory, relative=True)

        assert [track.name for track in tracks] == ["Ambient 1", "Ambient 2"]
        first, second = tracks
        assert [clip.name for clip in first.clips] == ["Г1_Эмбиент - Таверна.mp3", "Г1_Эмбиент - Таверна 2.mp3"]
        assert [clip.name for clip in second.clips] == ["Г1_Эмбиент - Лес.mp3"]
        assert [clip.start for clip in first.clips] == pytest.approx([0.0, 5.55])
        assert [clip.start for clip in second.clips] == pytest.approx([3.7])
        for clip in first.clips + second.clips:
            assert clip.source_in == 0.0
            assert clip.source_out == pytest.approx(180.0), "трек целиком"
            assert clip.relative == clip.name
            assert clip.path.endswith(clip.name)

    def test_without_the_relative_flag_the_clip_has_no_relative_name(self, factory):
        _scenes(factory)
        _go(factory)

        tracks = _tracks_of(factory)

        assert all(clip.relative == "" for track in tracks for clip in track.clips)

    def test_the_track_of_a_dismissed_scene_stays_out(self, factory):
        ids = _scenes(factory)
        _go(factory)
        with factory() as db:
            dismiss_marker(db, ids[1])
            db.commit()

        first, second = _tracks_of(factory)

        assert [clip.name for clip in first.clips] == ["Г1_Эмбиент - Таверна.mp3"]
        assert [clip.name for clip in second.clips] == ["Г1_Эмбиент - Таверна 2.mp3"]

    def test_failed_and_replaced_tracks_stay_out(self, factory):
        ids = _scenes(factory)
        _go(factory)
        with factory() as db:
            for track in db.query(AmbientTrack).filter(AmbientTrack.marker_id != ids[0]):
                track.status = "failed" if track.marker_id == ids[1] else "replaced"
            db.commit()

        tracks = _tracks_of(factory)

        assert [[clip.name for clip in track.clips] for track in tracks] == [["Г1_Эмбиент - Таверна.mp3"], []]

    def test_the_tracks_come_before_the_hand_made_ones(self, factory):
        from app.services.chapter_delivery import build_chapter_session

        _scenes(factory)
        _go(factory)
        with factory() as db:
            xml = build_chapter_session(db, "c1", relative=True)

        names = [line.strip() for line in xml.splitlines() if line.strip().startswith("<name>")]
        assert names.index("<name>Ambient 1</name>") < names.index("<name>Ambient 2</name>") \
            < names.index("<name>Саундтрек 1</name>")
        assert names.index("<name>Тупуг</name>") < names.index("<name>Ambient 1</name>")
        assert "Г1_Эмбиент - Лес.mp3" in xml

    def test_a_chapter_without_ambient_builds_the_same_session_as_before(self, factory, monkeypatch):
        from app.services import chapter_delivery
        from app.services.chapter_delivery import build_chapter_session, session_fingerprint

        ids = _scenes(factory)
        with factory() as db:
            before = build_chapter_session(db, "c1", relative=True)
        # Неудавшийся трек и трек без файла — не повод заводить дорожки.
        with factory() as db:
            db.add(AmbientTrack(book_id=BOOK, chapter_id="c1", marker_id=ids[0], status="failed"))
            db.add(AmbientTrack(book_id=BOOK, chapter_id="c1", marker_id=ids[1], status="done",
                                audio_file_id="missing"))
            db.commit()
            after = build_chapter_session(db, "c1", relative=True)
        monkeypatch.setattr(chapter_delivery, "ambient_session_tracks", lambda *a, **k: [])
        with factory() as db:
            untouched = build_chapter_session(db, "c1", relative=True)

        assert "Ambient" not in after
        assert session_fingerprint(after) == session_fingerprint(before) == session_fingerprint(untouched)

    def test_the_fingerprint_is_stable_across_rebuilds(self, factory):
        from app.services.chapter_delivery import build_chapter_session, session_fingerprint

        _scenes(factory)
        _go(factory)
        with factory() as db:
            first = build_chapter_session(db, "c1", relative=True)
            second = build_chapter_session(db, "c1", relative=True)

        assert "Ambient 1" in first
        assert session_fingerprint(first) == session_fingerprint(second)

    def test_a_broken_ambient_does_not_block_the_session(self, factory, monkeypatch):
        from app.services import chapter_delivery
        from app.services.chapter_delivery import build_chapter_session

        _scenes(factory)
        _go(factory)

        def broken(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(chapter_delivery, "ambient_session_tracks", broken)
        with factory() as db:
            xml = build_chapter_session(db, "c1")

        assert xml is not None and "Ambient" not in xml and "Саундтрек 1" in xml


class TestRecordingDoesNotSeeTheAmbient:
    def _generated(self, factory):
        _scenes(factory)
        _go(factory)
        with factory() as db:
            assert len(_ambient_files(db)) == 3

    def test_book_and_chapter_status_count_only_the_takes(self, factory):
        from app.services.chapter_delivery import book_recording_status, chapter_recording_status

        self._generated(factory)
        with factory() as db:
            row = next(r for r in book_recording_status(db, BOOK)["chapters"] if r["chapter_id"] == "c1")
            chapter = chapter_recording_status(db, "c1")

        assert row["integrity_files"] == 3 and row["recorded_roles"] == row["total_roles"]
        assert row["unmirrored_files"] + row["mirrored_files"] == 3
        assert "Эмбиент" not in [r["role"] for r in chapter["roles"]]
        assert chapter["orphan_takes"] == []
        assert sum(r["files"] for r in chapter["roles"]) == 3

    def test_the_chapter_archive_carries_the_ambient_apart_from_the_takes(self, factory, tmp_path):
        from app.services.chapter_delivery import build_chapter_archive

        self._generated(factory)
        target = tmp_path / "chapter.zip"
        with factory() as db:
            result = build_chapter_archive(db, "c1", str(target))

        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            manifest = archive.read("опись.txt").decode("utf-8")
            session = archive.read("сессия.sesx").decode("utf-8")
            forest = archive.read("Г1_Эмбиент - Лес.mp3")
        ambient_names = sorted(name for name in names if "Эмбиент" in name)
        assert ambient_names == ["Г1_Эмбиент - Лес.mp3", "Г1_Эмбиент - Таверна 2.mp3", "Г1_Эмбиент - Таверна.mp3"]
        assert forest.startswith(b"ID3")
        for name in ambient_names:
            assert name in session, "сессия зовёт трек тем же именем, что в архиве"
        head, _sep, section = manifest.partition("\nЭмбиент\n")
        assert "Эмбиент" not in head, "эмбиент не среди дублей"
        assert all(name in section for name in ambient_names)
        # в счёт записи эмбиент не идёт
        assert not any("Эмбиент" in name for name in result["files_missing"])
        assert result["files_written"] + len(result["files_missing"]) == 3
        assert (result["ambient_written"], result["ambient_missing"]) == (3, [])
        assert (result["recorded_roles"], result["total_roles"]) == (3, 3)

    def test_a_dismissed_scene_track_stays_out_of_the_archive(self, factory, tmp_path):
        from app.services.chapter_delivery import build_chapter_archive

        ids = _scenes(factory)
        _go(factory)
        with factory() as db:
            dismiss_marker(db, ids[1])
            db.commit()
        target = tmp_path / "chapter.zip"
        with factory() as db:
            build_chapter_archive(db, "c1", str(target))

        with zipfile.ZipFile(target) as archive:
            assert "Г1_Эмбиент - Лес.mp3" not in archive.namelist()

    def test_the_character_map_has_no_ambient_role(self, factory):
        from app.v2.character_map import character_map

        self._generated(factory)
        with factory() as db:
            names = [row["name"] for row in character_map(db, BOOK)]

        assert "Эмбиент" not in names

    def test_the_recent_files_of_the_chapter_skip_the_ambient(self, factory):
        from app.services.recording_workspace import build_recording_workspace_context

        self._generated(factory)
        with factory() as db:
            db.get(ScriptChapter, "c1").status = "published"
            db.commit()
            context = build_recording_workspace_context(db, selected_book_id=BOOK, selected_chapter_id="c1")

        assert context["recent_files_current_chapter"], "дубли главы видны"
        assert all(item.kind == "take" for item in context["recent_files"])
        assert all(item.kind == "take" for item in context["recent_files_current_chapter"])

    def test_the_recent_files_filter_drops_an_ambient_row(self):
        from app.services.recording_workspace import _recent_files_for_selected_chapter

        chapter = ScriptChapter(id="c", book_id="b", chapter_index=1, chapter_title="1")
        take = AudioFile(chapter="1", role="Гамук", kind="take")
        ambient = AudioFile(chapter="1", role="Эмбиент", kind="ambient")

        assert _recent_files_for_selected_chapter([take, ambient], chapter) == [take]


class TestTheNasSessionWaitsForABrokenAmbientCopy:
    """Проект на NAS зовёт треки эмбиента по имени, как дубли: битая копия трека на NAS
    держит `.sesx`, как битая копия дубля; ещё не доехавший трек — нет."""

    def _chapter(self, db, monkeypatch, mirror_state):
        import tests.test_chapter_session_archive as archive_tests
        from app.models import SoundMarker

        _book_id, chapter_id = archive_tests._book_with_one_chapter(db, roles={"Дгарнин": 5})
        archive_tests._CHAPTER_ID = chapter_id
        archive_tests._take(db, chapter_id, role="Дгарнин", mirror_state="ok")
        chapter = db.get(ScriptChapter, chapter_id)
        audio = AudioFile(book_code="KP", original_filename="a.mp3", canonical_filename="Г5_Эмбиент - Лес.mp3",
                          stored_key="KP/Chapter05/Г5_Эмбиент - Лес.mp3", mime_type="audio/mpeg",
                          size_bytes=10, md5="x" * 32, chapter="Глава 5. Проба пера", role="Эмбиент",
                          kind="ambient", duration_seconds=180.0, mirror_state=mirror_state)
        marker = SoundMarker(book_id=chapter.book_id, chapter_id=chapter_id, segment_id=f"{chapter_id}:00000",
                             kind="scene", payload={}, text_sha256="x")
        db.add_all([audio, marker])
        db.flush()
        db.add(AmbientTrack(book_id=chapter.book_id, chapter_id=chapter_id, marker_id=marker.id,
                            audio_file_id=audio.id, status="done"))
        db.commit()
        archive_tests._finished(db, monkeypatch)
        return chapter_id

    def test_a_mismatched_ambient_copy_blocks_the_session(self, roots, monkeypatch):
        from app.services.audio_mirror import MISMATCH
        from app.services.chapter_delivery import archive_chapter_session
        from tests.test_chapter_session_archive import _sessions

        with _sessions()() as db:
            chapter_id = self._chapter(db, monkeypatch, MISMATCH)
            result = archive_chapter_session(db, chapter_id)

        assert result == {"written": False, "reason": "mirror_broken", "replaced": ""}

    def test_an_unmirrored_ambient_copy_does_not_block(self, roots, monkeypatch):
        from app.services.chapter_delivery import archive_chapter_session
        from tests.test_chapter_session_archive import _sessions

        with _sessions()() as db:
            chapter_id = self._chapter(db, monkeypatch, "")
            result = archive_chapter_session(db, chapter_id)

        assert result["written"] is True


class TestTheAmbientFilesAreVerifiedToo:
    """Сверка главы перечитывает и треки эмбиента: они лежат в той же папке, что и
    дубли, идут в тот же архив, и битый mp3 в проекте звукорежиссёра — та же беда.
    Но это не работа диктора: у главы свой счётчик, и дубли от него не краснеют."""

    def _generated(self, factory):
        _scenes(factory)
        _go(factory)

    def _break_one(self, factory):
        from app.services import audio_storage

        with factory() as db:
            item = sorted(_ambient_files(db).values(), key=lambda row: row.canonical_filename)[0]
            path = audio_storage.resolve_path(item.stored_key, location=str(item.location or "local"))
        with open(path, "ab") as handle:
            handle.write(b"tail")

    def test_verifying_the_chapter_reads_the_ambient(self, factory):
        from app.services.audio_integrity import verify_chapter

        self._generated(factory)
        with factory() as db:
            result = verify_chapter(db, "c1")
            db.commit()
            states = {item.verify_state for item in _ambient_files(db).values()}

        assert result["ambient_checked"] == 3 and result["ambient_bad"] == 0
        assert states == {"ok"}

    def test_a_broken_track_blocks_the_archive_but_not_the_takes(self, factory):
        from app.services.audio_integrity import chapter_has_broken_files, verify_chapter
        from app.services.chapter_delivery import book_recording_status

        self._generated(factory)
        self._break_one(factory)
        with factory() as db:
            result = verify_chapter(db, "c1")
            db.commit()
            broken = chapter_has_broken_files(db, "c1")
            row = next(r for r in book_recording_status(db, BOOK, ambient=True)["chapters"]
                       if r["chapter_id"] == "c1")

        assert result["ambient_bad"] == 1
        assert broken is True
        assert row["ambient_broken"] == 1
        assert row["integrity_bad"] == 0, "дубли целы — диктору перезаливать нечего"
