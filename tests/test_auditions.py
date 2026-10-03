"""Пробы, собранные для прослушивания.

Слушают их все, кто книгу читает: услышать роль в чужом исполнении полезно каждому.
Решают — чей это голос — владелец и автор.

Книга в `audio_files` названа кодом, выведенным из заголовка («Крылья полумрака» →
«КП»), а каст — идентификатором книги. Перевод между ними — забота этого модуля, а не
экрана: экран знает книгу, которую открыл.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.audio_uploads as au
from app.db import Base
from app.models import ScriptBook
from app.services.auditions import book_auditions, find_audition, inline_disposition, slice_for_range
from app.time_utils import utcnow_naive


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
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _book(db, title="Крылья полумрака"):
    book = ScriptBook(title=title, source_filename=f"{title}.txt", source_format="txt", created_at=utcnow_naive())
    db.add(book)
    db.flush()
    return book


def _upload(db, *, role, actor, kind="audition", chapter=""):
    return au.store_audio_file(
        db, payload=b"x" * 10, mime_type="audio/wav", original_filename=f"{role}.wav",
        book_code="КП", chapter=chapter, role=role, actor_name=actor, kind=kind, safe_name=lambda s: s,
    )


def _seeded():
    sessions = _db()
    original, au.audio_storage = au.audio_storage, _FakeStorage()
    db = sessions()
    book = _book(db)
    _upload(db, role="Сатухух", actor="Роман Сомов")
    _upload(db, role="Сатухух", actor="София Ершова")
    _upload(db, role="Эней", actor="София Ершова")
    _upload(db, role="Химера", actor="Роман Сомов", kind="take", chapter="Глава4")
    db.commit()
    au.audio_storage = original
    return db, book


class TestWhatComesBack:
    def test_only_auditions_never_the_takes(self):
        db, book = _seeded()
        rows = book_auditions(db, book_id=book.id)

        assert len(rows) == 3
        assert all(row["role"] != "Химера" for row in rows)

    def test_the_newest_is_first(self):
        db, book = _seeded()
        rows = book_auditions(db, book_id=book.id)

        assert rows[0]["role"] == "Эней"

    def test_a_row_says_who_read_and_what_the_file_was_called(self):
        db, book = _seeded()
        row = next(item for item in book_auditions(db, book_id=book.id) if item["role"] == "Эней")

        assert row["actor_name"] == "София Ершова"
        assert row["original_filename"] == "Эней.wav"
        assert row["canonical_filename"] == "KP_Eney_SofiyaErshova_proba.wav"
        assert row["id"]

    def test_a_book_nobody_has_uploaded_for_is_empty_not_missing(self):
        db, _ = _seeded()
        other = _book(db, title="Сказки волшебников")
        db.flush()

        assert book_auditions(db, book_id=other.id) == []

    def test_a_book_that_does_not_exist_is_missing(self):
        db, _ = _seeded()

        assert book_auditions(db, book_id="no-such-book") is None


class TestFindingOne:
    def test_one_file_is_found_by_its_id(self):
        db, book = _seeded()
        wanted = book_auditions(db, book_id=book.id)[0]

        assert find_audition(db, wanted["id"]).canonical_filename == wanted["canonical_filename"]

    def test_a_take_is_not_an_audition_even_by_id(self):
        db, book = _seeded()
        take = db.query(au.AudioFile).filter(au.AudioFile.kind == "take").one()

        assert find_audition(db, take.id) is None


class TestSeekingInsideAFile:
    """`<audio>` перематывает запросом `Range`; без него ползунок не двигается."""

    def test_a_plain_request_asks_for_no_slice(self):
        assert slice_for_range("", 1000) is None

    def test_a_range_from_the_middle_to_the_end(self):
        assert slice_for_range("bytes=400-", 1000) == (400, 999)

    def test_a_bounded_range(self):
        assert slice_for_range("bytes=0-99", 1000) == (0, 99)

    def test_a_range_running_past_the_end_stops_at_it(self):
        assert slice_for_range("bytes=900-5000", 1000) == (900, 999)

    def test_nonsense_is_treated_as_no_range(self):
        assert slice_for_range("bytes=abc", 1000) is None
        assert slice_for_range("items=0-10", 1000) is None
        assert slice_for_range("bytes=5000-6000", 1000) is None


def test_a_cyrillic_filename_survives_the_header():
    """Заголовки HTTP — latin-1; имя как есть уронило бы ответ до отправки."""
    header = inline_disposition("КП_Проба_Сатухух_Сомов.wav")

    header.encode("latin-1")
    assert header.startswith("inline; filename*=UTF-8''")
    assert "%D0" in header
