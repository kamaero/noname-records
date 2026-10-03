"""Воркер не идёт к NAS-файлам, пока разовая проба не подтвердила, что NAS жив.

Сторож с флагом живёт только в веб-процессе; воркер про NAS не знает ничего. 27.09 NAS
пропал на сутки — любое задание, дошедшее до старой записи на нём, висело бы на
монтировании (или, после перевода на `soft`, ждало бы ошибки по полминуты на файл).
"""
import threading
import time

import pytest

from app.services import nas_health


class TestReachableNow:
    def test_a_probe_that_answers_in_time_says_alive(self, monkeypatch):
        monkeypatch.setattr(nas_health, "probe_once", lambda root: True)
        assert nas_health.reachable_now(timeout=1.0) is True

    def test_a_probe_that_hangs_is_given_up_on_after_the_timeout(self, monkeypatch):
        """Главный случай: монтирование молчит. Ждём не дольше таймаута и отвечаем «нет»."""
        release = threading.Event()
        monkeypatch.setattr(nas_health, "probe_once", lambda root: release.wait(30))
        started = time.monotonic()
        try:
            assert nas_health.reachable_now(timeout=0.2) is False
            assert time.monotonic() - started < 2.0
        finally:
            release.set()

    def test_a_probe_that_fails_fast_says_no(self, monkeypatch):
        monkeypatch.setattr(nas_health, "probe_once", lambda root: False)
        assert nas_health.reachable_now(timeout=1.0) is False

    def test_a_probe_that_raises_says_no(self, monkeypatch):
        def boom(root):
            raise OSError("EIO")

        monkeypatch.setattr(nas_health, "probe_once", boom)
        assert nas_health.reachable_now(timeout=1.0) is False


class TestAsrOfATakeOnTheNas:
    @pytest.fixture()
    def studio(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        import app.models
        import app.v2.models
        from app.db import Base
        from tests.asr_studio import make_chapter

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        with sessionmaker(bind=engine)() as db:
            chapter_id = make_chapter(db, [("Дгарнин", "Займ под особые условия.")], {"Дгарнин": "Сергей Зотов"})
            yield db, chapter_id

    def _take(self, db, location):
        from app.models import AudioFile
        from app.time_utils import utcnow_naive
        from tests.asr_studio import CHAPTER_TITLE

        db.add(AudioFile(id="t1", book_code="КП", original_filename="d.wav", stored_key="k/t1.wav",
                         canonical_filename="KP_Ch11_Dgarnin.wav", mime_type="audio/wav", size_bytes=10,
                         chapter=CHAPTER_TITLE, role="Дгарнин", actor_name="Сергей Зотов", kind="take",
                         location=location, uploaded_at=utcnow_naive()))
        db.flush()

    def test_a_silent_nas_fails_the_job_with_a_reason_instead_of_reading(self, studio, monkeypatch):
        from app.services.asr_run import run_asr_for_take

        db, _ = studio
        self._take(db, "nas")
        monkeypatch.setattr(nas_health, "reachable_now", lambda timeout=None: False)
        heard = []

        job = run_asr_for_take(db, "t1", transcribe=lambda path: heard.append(path) or {"segments": []}, notify=False)

        assert heard == [], "распознавание полезло на мёртвый NAS"
        assert job.status == "failed"
        assert "storage_unavailable" in job.error_message

    def test_a_local_take_never_asks_about_the_nas(self, studio, monkeypatch):
        from app.services.asr_run import run_asr_for_take
        from tests.asr_studio import heard

        db, _ = studio
        self._take(db, "local")
        asked = []
        monkeypatch.setattr(nas_health, "reachable_now", lambda timeout=None: asked.append(1) or False)

        job = run_asr_for_take(db, "t1", transcribe=heard("Займ под особые условия."), notify=False)

        assert asked == []
        assert job.status == "done"


