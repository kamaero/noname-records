"""Проба живёт в той же таблице, что и дубль, и отличается одним полем.

Отдельная таблица развела бы их навсегда: у пробы и дубля одна и та же природа —
файл, книга, роль, актёр, — и разное только назначение. `kind` — это назначение.
Оно же решает, можно ли прислать второй файл на то же место: дубль главы один,
попыток на роль столько, сколько актёр захочет.
"""
import app.services.audio_uploads as au
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile


class _FakeStorage:
    """Хранилище знает и куда положило, и что именно: разбор файла спрашивает путь,
    сверка — сумму."""

    def write_file(self, key, payload, location="nas"):
        import hashlib
        return hashlib.md5(payload).hexdigest()

    def write_stream(self, key, source, chunk_size=1024 * 1024, location="nas"):
        import hashlib
        digest = hashlib.md5()
        written = 0
        while True:
            chunk = source.read(65536)
            if not chunk:
                break
            digest.update(chunk)
            written += len(chunk)
        return written, digest.hexdigest()

    def resolve_path(self, key, location="nas"):
        return f"/nowhere/{key}"

    def local_root(self):
        return "/nowhere"

    def free_bytes(self, path):
        # Сторож места (задача 5) спрашивает диск при каждом приёме — этому
        # фейку интересна только сама запись, поэтому места всегда с запасом.
        return 10**12


def _sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _store(db, **kwargs):
    base = dict(
        payload=b"x", mime_type="audio/wav", original_filename="t.wav",
        book_code="КП", chapter="", role="Сатухух", actor_name="Сомов", safe_name=lambda s: s,
    )
    base.update(kwargs)
    return au.store_audio_file(db, **base)


def _with_storage(fn):
    original = au.audio_storage
    au.audio_storage = _FakeStorage()
    try:
        fn()
    finally:
        au.audio_storage = original


def test_a_take_is_a_take_without_being_told():
    def body():
        sessions = _sessions()
        with sessions() as db:
            item = _store(db, chapter="Глава4")
            db.commit()
            assert item.kind == "take"
    _with_storage(body)


def test_an_audition_keeps_the_chapter_it_was_read_from():
    """Требовать главу нельзя, а назвать — можно: полезно знать, кто какой кусок читал."""
    def body():
        sessions = _sessions()
        with sessions() as db:
            item = _store(db, kind="audition", chapter="Глава2")
            db.commit()
            assert item.kind == "audition"
            assert item.chapter == "Глава2"
            assert item.canonical_filename == "KP_Ch02_Satuhuh_Somov_proba.wav"
    _with_storage(body)


def test_an_audition_read_from_nowhere_in_particular():
    def body():
        sessions = _sessions()
        with sessions() as db:
            item = _store(db, kind="audition")
            db.commit()
            assert item.canonical_filename == "KP_Satuhuh_Somov_proba.wav"
    _with_storage(body)


def test_a_second_attempt_at_the_same_role_lands_beside_the_first():
    def body():
        sessions = _sessions()
        with sessions() as db:
            first = _store(db, kind="audition")
            second = _store(db, kind="audition")
            third = _store(db, kind="audition")
            db.commit()
            names = [first.canonical_filename, second.canonical_filename, third.canonical_filename]
            assert names == [
                "KP_Satuhuh_Somov_proba.wav",
                "KP_Satuhuh_Somov_proba_2.wav",
                "KP_Satuhuh_Somov_proba_3.wav",
            ]
            assert db.query(AudioFile).count() == 3
    _with_storage(body)


def test_another_actor_starts_his_own_count():
    def body():
        sessions = _sessions()
        with sessions() as db:
            _store(db, kind="audition")
            other = _store(db, kind="audition", actor_name="Ершова")
            db.commit()
            assert other.canonical_filename == "KP_Satuhuh_Ershova_proba.wav"
    _with_storage(body)


def test_the_chapters_recent_files_do_not_show_auditions():
    """«Последние файлы главы» — про главу; у пробы главы нет, и в пустой она бы всплыла."""
    from types import SimpleNamespace

    from app.services.recording_workspace import _recent_files_for_selected_chapter

    chapter = SimpleNamespace(chapter_title="")
    take = SimpleNamespace(chapter="", kind="take", role="Химера")
    audition = SimpleNamespace(chapter="", kind="audition", role="Сатухух")

    kept = _recent_files_for_selected_chapter([take, audition], chapter)

    assert [item.role for item in kept] == ["Химера"]


def test_the_upload_time_does_not_move_when_the_row_is_edited():
    """`uploaded_at` — когда файл прислали, а не когда мы его в последний раз тронули.

    На колонке стоял `onupdate`, и переклассификация проб задним числом переписала
    время загрузки двадцати одному файлу: в «Последних файлах главы» они всплыли
    свежими, будто их только что прислали. Отметка о событии не меняется оттого, что
    мы правим строку.
    """
    def body():
        sessions = _sessions()
        with sessions() as db:
            item = _store(db, chapter="Глава4")
            db.commit()
            when = item.uploaded_at

            item.kind = "audition"
            item.canonical_filename = "KP_Satuhuh_Somov_proba.wav"
            db.commit()

            assert item.uploaded_at == when
    _with_storage(body)


def test_a_take_can_be_stored_from_a_stream_instead_of_bytes():
    """Гигабайтный дубль незачем держать в памяти, чтобы записать его на диск."""
    import io as _io
    import os
    import tempfile

    from app.services import audio_storage

    root = tempfile.mkdtemp()
    original_root = audio_storage.nas_root
    audio_storage.nas_root = lambda: root
    original = au.audio_storage
    au.audio_storage = audio_storage
    try:
        sessions = _sessions()
        with sessions() as db:
            item = au.store_audio_file(
                db, source=_io.BytesIO(b"x" * 5000), mime_type="audio/wav",
                original_filename="rasskazchik.wav", book_code="КП", chapter="Глава4",
                role="Рассказчик", actor_name="Иван Гончаров", safe_name=lambda s: s,
            )
            db.commit()
            assert item.size_bytes == 5000
            assert os.path.getsize(audio_storage.resolve_path(item.stored_key)) == 5000
    finally:
        au.audio_storage = original
        audio_storage.nas_root = original_root


def test_storing_needs_either_bytes_or_a_stream():
    import pytest as _pytest

    def body():
        sessions = _sessions()
        with sessions() as db:
            with _pytest.raises(ValueError, match="payload_or_source"):
                au.store_audio_file(
                    db, mime_type="audio/wav", original_filename="x.wav", book_code="КП",
                    chapter="Глава4", role="Роль", actor_name="Кто-то", safe_name=lambda s: s,
                )
    _with_storage(body)
