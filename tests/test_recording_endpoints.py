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


def _db():
    e = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(e)
    return sessionmaker(bind=e)


def test_role_take_then_patch_coexist_by_key():
    orig = au.audio_storage; au.audio_storage = _FakeStorage()
    try:
        S = _db()
        with S() as db:
            au.store_audio_file(db, payload=b"x", mime_type="audio/wav", original_filename="t.wav",
                book_code="B", chapter="2", role="Дгарнин", actor_name="A", safe_name=lambda s: s)
            au.store_audio_file(db, payload=b"x", mime_type="audio/wav", original_filename="p.wav",
                book_code="B", chapter="2", role="Дгарнин", actor_name="A", safe_name=lambda s: s, line_index=13)
            db.commit()
            rows = db.query(AudioFile).filter(AudioFile.role == "Дгарнин").all()
            assert len([r for r in rows if r.line_index is None]) == 1
            assert len([r for r in rows if r.line_index == 13]) == 1
    finally:
        au.audio_storage = orig
