from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def test_audiofile_line_index_defaults_null_and_accepts_int():
    SessionLocal = _session()
    with SessionLocal() as db:
        take = AudioFile(book_code="B", original_filename="t.wav", stored_key="k1",
                         mime_type="audio/wav", size_bytes=1, chapter="2", role="Дгарнин")
        patch = AudioFile(book_code="B", original_filename="p.wav", stored_key="k2",
                          mime_type="audio/wav", size_bytes=1, chapter="2", role="Дгарнин", line_index=13)
        db.add_all([take, patch]); db.commit()
        assert take.line_index is None
        assert patch.line_index == 13


def test_store_audio_file_sets_line_index():
    import app.services.audio_uploads as au
    SessionLocal = _session()

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

    orig = au.audio_storage
    au.audio_storage = _FakeStorage()
    try:
        with SessionLocal() as db:
            take = au.store_audio_file(db, payload=b"x", mime_type="audio/wav",
                original_filename="t.wav", book_code="B", chapter="2", role="Дгарнин",
                actor_name="A", safe_name=lambda s: s)
            patch = au.store_audio_file(db, payload=b"x", mime_type="audio/wav",
                original_filename="p.wav", book_code="B", chapter="2", role="Дгарнин",
                actor_name="A", safe_name=lambda s: s, line_index=13)
            db.commit()
            assert take.line_index is None
            assert patch.line_index == 13
    finally:
        au.audio_storage = orig
