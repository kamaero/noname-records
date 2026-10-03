import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import AudioFile, Base, ScriptBook, ScriptChapter
from app.services import audio_integrity
from app.time_utils import utcnow_naive


def _sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_a_fresh_file_is_unverified_and_lives_on_the_local_disk():
    """Пустая сумма — честное «не считали». Ноль или «ok» соврали бы о проверке."""
    sessions = _sessions()
    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="a.wav", stored_key="uploads/raw/2026/09/a.wav",
            mime_type="audio/wav", size_bytes=1, chapter="Глава1", role="Роль",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        assert item.md5 == ""
        assert item.location == "local"
        assert item.verified_at is None
        assert item.verify_state == ""


def test_the_stored_record_keeps_the_digest_of_the_uploaded_bytes():
    """Сумма без записи в базе бесполезна: сверять будет не с чем."""
    import hashlib
    import io

    import app.services.audio_uploads as au

    sessions = _sessions()
    payload = b"take-bytes" * 1000

    class _FakeStorage:
        def write_file(self, key, data, location="nas"):
            return hashlib.md5(data).hexdigest()

        def write_stream(self, key, source, chunk_size=1024 * 1024, location="nas"):
            data = source.read()
            return len(data), hashlib.md5(data).hexdigest()

        def resolve_path(self, key, location="nas"):
            return f"/nowhere/{key}"

        def local_root(self):
            return "/nowhere"

        def free_bytes(self, path):
            # Сторож места (задача 5) спрашивает диск при каждом приёме — этому
            # фейку интересна только сама запись, поэтому места всегда с запасом.
            return 10**12

    original = au.audio_storage
    au.audio_storage = _FakeStorage()
    try:
        with sessions() as db:
            item = au.store_audio_file(
                db, source=io.BytesIO(payload), mime_type="audio/wav",
                original_filename="a.wav", book_code="KP", chapter="Глава1",
                role="Роль", actor_name="Актёр", safe_name=lambda name: name,
            )
            db.commit()
            assert item.md5 == hashlib.md5(payload).hexdigest()
            assert item.size_bytes == len(payload)
    finally:
        au.audio_storage = original


def test_a_file_whose_bytes_match_its_stored_digest_is_ok(tmp_path, monkeypatch):
    import hashlib

    from app.services import audio_integrity, audio_storage

    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
    payload = b"clean take"
    key = "uploads/raw/2026/09/a.wav"
    (tmp_path / "uploads/raw/2026/09").mkdir(parents=True)
    (tmp_path / key).write_bytes(payload)

    sessions = _sessions()
    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="a.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=len(payload), chapter="Глава1", role="Роль",
            md5=hashlib.md5(payload).hexdigest(),
        )
        db.add(item)
        db.commit()
        assert audio_integrity.verify_file(db, item) == "ok"
        assert item.verify_state == "ok"
        assert item.verified_at is not None


def test_a_truncated_file_is_caught(tmp_path, monkeypatch):
    """Ровно тот случай, ради которого всё затевалось: обрыв на середине давал
    запись «загружено» и обрезок, который проявился бы тишиной на сборке главы."""
    import hashlib

    from app.services import audio_integrity, audio_storage

    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
    key = "uploads/raw/2026/09/b.wav"
    (tmp_path / "uploads/raw/2026/09").mkdir(parents=True)
    (tmp_path / key).write_bytes(b"half")

    sessions = _sessions()
    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="b.wav", stored_key=key, mime_type="audio/wav",
            size_bytes=9, chapter="Глава1", role="Роль",
            md5=hashlib.md5(b"half a take").hexdigest(),
        )
        db.add(item)
        db.commit()
        assert audio_integrity.verify_file(db, item) == "mismatch"


def test_a_vanished_file_is_reported_as_missing(tmp_path, monkeypatch):
    from app.services import audio_integrity, audio_storage

    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
    sessions = _sessions()
    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="c.wav", stored_key="uploads/raw/2026/09/c.wav",
            mime_type="audio/wav", size_bytes=1, chapter="Глава1", role="Роль", md5="ff" * 16,
        )
        db.add(item)
        db.commit()
        assert audio_integrity.verify_file(db, item) == "missing"


def test_a_file_from_before_the_migration_is_skipped_not_blamed(tmp_path, monkeypatch):
    """У старых записей суммы нет. Объявить их битыми — соврать про два десятка
    честных файлов и приучить владельца не верить красной отметке."""
    from app.services import audio_integrity, audio_storage

    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
    sessions = _sessions()
    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="d.wav", stored_key="uploads/raw/2026/09/d.wav",
            mime_type="audio/wav", size_bytes=1, chapter="Глава1", role="Роль", md5="",
        )
        db.add(item)
        db.commit()
        assert audio_integrity.verify_file(db, item) == "skipped"
        assert item.verify_state == ""


def test_verify_file_skips_a_nas_row_when_the_mirror_is_unreachable(tmp_path, monkeypatch):
    """Мёртвое зеркало не должно мешать сверке того, что физически на сервере.

    `location='nas'` — это только у наследия до переезда на локальный диск: такую
    строку без живого NAS прочитать нечем, и честный ответ — `skipped`, а не
    `missing` (файл никуда не делся, до него просто не достать сейчас) и не провал
    сверки остальных файлов главы.
    """
    import hashlib

    from app.services import audio_integrity, audio_storage

    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
    sessions = _sessions()
    with sessions() as db:
        item = AudioFile(
            book_code="KP", original_filename="e.wav", stored_key="uploads/raw/2026/09/e.wav",
            mime_type="audio/wav", size_bytes=1, chapter="Глава1", role="Роль", location="nas",
            md5=hashlib.md5(b"whatever").hexdigest(),
        )
        db.add(item)
        db.commit()
        assert audio_integrity.verify_file(db, item, nas_reachable=False) == "skipped"
        # Ни вердикт, ни отметка о времени сверки не пишутся: когда зеркало
        # оживёт, файл должен проверяться заново, как будто его ещё не трогали.
        assert item.verify_state == ""
        assert item.verified_at is None


def test_verify_chapter_leaves_local_files_alone_when_the_mirror_is_down(tmp_path, monkeypatch):
    """Смешанная глава: локальный дубль сверяется как обычно, дубль на мёртвом
    зеркале — честно `skipped`. Раньше недоступность NAS отменяла сверку целиком,
    хотя большинство дублей давно лежит на диске сервера."""
    import hashlib

    from app.services import audio_integrity, audio_storage
    from app.services.chapter_delivery import _chapter_label

    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
    dest_dir = tmp_path / "uploads/raw/2026/09"
    dest_dir.mkdir(parents=True)
    payload = b"local take, lives on this server"
    (dest_dir / "local.wav").write_bytes(payload)
    # "nas.wav" намеренно не создаётся ни на локальном корне, ни на NAS: файл
    # физически на зеркале, и локальный корень его не видит вовсе.

    sessions = _sessions()
    with sessions() as db:
        book = ScriptBook(title="Крылья", source_filename="k.txt", source_format="txt")
        db.add(book)
        db.commit()
        chapter = ScriptChapter(book_id=book.id, chapter_index=1, chapter_title="Глава 1")
        db.add(chapter)
        db.commit()
        label = _chapter_label(chapter)

        db.add_all([
            AudioFile(
                book_code="KP", original_filename="local.wav", stored_key="uploads/raw/2026/09/local.wav",
                mime_type="audio/wav", size_bytes=len(payload), chapter=label, role="Роль1", location="local",
                md5=hashlib.md5(payload).hexdigest(),
            ),
            AudioFile(
                book_code="KP", original_filename="nas.wav", stored_key="uploads/raw/2026/09/nas.wav",
                mime_type="audio/wav", size_bytes=1, chapter=label, role="Роль2", location="nas",
                md5="ab" * 16,
            ),
        ])
        db.commit()

        result = audio_integrity.verify_chapter(db, chapter.id, nas_reachable=False)
        assert result == {"checked": 2, "ok": 1, "mismatch": 0, "missing": 0, "skipped": 1,
                          "ambient_checked": 0, "ambient_bad": 0}


def test_chapter_verdict_counts_every_outcome(tmp_path, monkeypatch):
    """Сводка по главе — три дубля, три разных вердикта. Метка главы взята той же
    `_chapter_label`, что и продакшен-код, иначе тест разойдётся с реальностью на
    первой же главе с непустым заголовком."""
    import hashlib

    from app.services import audio_integrity, audio_storage
    from app.services.chapter_delivery import _chapter_label

    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path))
    dest_dir = tmp_path / "uploads/raw/2026/09"
    dest_dir.mkdir(parents=True)

    whole = b"clean chapter take"
    (dest_dir / "whole.wav").write_bytes(whole)
    (dest_dir / "truncated.wav").write_bytes(b"half")
    # "missing.wav" не создаётся — файл, который должен пропасть.

    sessions = _sessions()
    with sessions() as db:
        book = ScriptBook(title="Крылья", source_filename="k.txt", source_format="txt")
        db.add(book)
        db.commit()
        chapter = ScriptChapter(book_id=book.id, chapter_index=1, chapter_title="Глава 1")
        db.add(chapter)
        db.commit()
        label = _chapter_label(chapter)

        db.add_all([
            AudioFile(
                book_code="KP", original_filename="whole.wav", stored_key="uploads/raw/2026/09/whole.wav",
                mime_type="audio/wav", size_bytes=len(whole), chapter=label, role="Роль1",
                md5=hashlib.md5(whole).hexdigest(),
            ),
            AudioFile(
                book_code="KP", original_filename="truncated.wav", stored_key="uploads/raw/2026/09/truncated.wav",
                mime_type="audio/wav", size_bytes=9, chapter=label, role="Роль2",
                md5=hashlib.md5(b"a full take").hexdigest(),
            ),
            AudioFile(
                book_code="KP", original_filename="missing.wav", stored_key="uploads/raw/2026/09/missing.wav",
                mime_type="audio/wav", size_bytes=1, chapter=label, role="Роль3", md5="ff" * 16,
            ),
        ])
        db.commit()

        result = audio_integrity.verify_chapter(db, chapter.id)
        assert result == {"checked": 3, "ok": 1, "mismatch": 1, "missing": 1, "skipped": 0,
                          "ambient_checked": 0, "ambient_bad": 0}


@pytest.fixture
def roles_are(monkeypatch):
    """Подменяет список ролей главы, а не собирает его руками из сегментов и
    атрибуций: `chapter_role_counts` — чужой код, тут проверяется свой."""

    def _set(mapping):
        monkeypatch.setattr(audio_integrity, "chapter_role_counts", lambda db, chapter_id: dict(mapping),
                             raising=False)

    return _set


def _chapter(db, title="Глава1") -> str:
    # У ScriptBook нет поля book_code (оно только у AudioFile) — source_filename и
    # source_format у книги обязательны без значения по умолчанию.
    book = ScriptBook(title="КП", source_filename="kp.txt", source_format="txt")
    db.add(book)
    db.flush()
    chapter = ScriptChapter(book_id=book.id, chapter_index=1, chapter_title=title)
    db.add(chapter)
    db.flush()
    return str(chapter.id)


def _take(db, chapter_id, *, role, verify_state="", location="nas", md5="ab" * 16) -> AudioFile:
    from app.services.chapter_delivery import _chapter_label

    chapter = db.get(ScriptChapter, chapter_id)
    item = AudioFile(
        book_code="KP", original_filename=f"{role}.wav", stored_key=f"uploads/raw/2026/09/{role}.wav",
        mime_type="audio/wav", size_bytes=1, chapter=_chapter_label(chapter), role=role,
        kind="take", verify_state=verify_state, location=location, md5=md5,
    )
    db.add(item)
    db.flush()
    return item


def test_a_chapter_is_ready_only_when_every_role_has_a_take(roles_are):
    """«Собрана» — это все говорящие роли, Рассказчик наравне со всеми. Одна роль
    из двадцати шести однажды уже показала стопроцентную готовность."""
    sessions = _sessions()
    with sessions() as db:
        roles_are({"Дгарнин": 10, "Рассказчик": 40})
        chapter_id = _chapter(db)
        _take(db, chapter_id, role="Дгарнин")
        db.commit()
        assert audio_integrity.chapter_is_ready(db, chapter_id) is False

        _take(db, chapter_id, role="Рассказчик")
        db.commit()
        assert audio_integrity.chapter_is_ready(db, chapter_id) is True


def test_a_verified_chapter_is_not_queued_again(roles_are):
    """Каждая новая проба не должна запускать пятиминутное перечитывание заново."""
    sessions = _sessions()
    with sessions() as db:
        roles_are({"Дгарнин": 10})
        chapter_id = _chapter(db)
        item = _take(db, chapter_id, role="Дгарнин")
        db.commit()
        assert audio_integrity.chapter_needs_verification(db, chapter_id) is True

        item.verified_at = utcnow_naive()
        db.add(item)
        db.commit()
        assert audio_integrity.chapter_needs_verification(db, chapter_id) is False


def test_a_chapter_of_files_without_a_receipt_sum_is_never_queued(roles_are):
    """Дубли, принятые до миграции 0020, сверять не с чем: `verify_file` честно
    отвечает `skipped` и не пишет `verified_at`, поэтому «ни один файл не сверяли»
    оставалось правдой навсегда — каждая новая загрузка ставила в очередь `high`
    пустое задание, а кнопка «сверить» рисовалась вечно."""
    sessions = _sessions()
    with sessions() as db:
        roles_are({"Дгарнин": 10})
        chapter_id = _chapter(db)
        _take(db, chapter_id, role="Дгарнин", md5="")
        db.commit()
        assert audio_integrity.chapter_needs_verification(db, chapter_id) is False


def test_one_file_with_a_receipt_sum_is_enough_to_queue_the_chapter(roles_are):
    """Смешанная глава — это всё ещё работа: дозаписанный дубль сумму приёма имеет."""
    sessions = _sessions()
    with sessions() as db:
        roles_are({"Дгарнин": 10})
        chapter_id = _chapter(db)
        _take(db, chapter_id, role="Дгарнин", md5="")
        _take(db, chapter_id, role="Дгарнин")
        db.commit()
        assert audio_integrity.chapter_needs_verification(db, chapter_id) is True


def test_a_chapter_with_a_mismatched_file_is_broken():
    """Третий предиктор изначально остался без своего теста — заглушка в
    маршрутном тесте архива доказывает только проводку, а не саму логику."""
    sessions = _sessions()
    with sessions() as db:
        chapter_id = _chapter(db)
        _take(db, chapter_id, role="Дгарнин", verify_state="ok")
        _take(db, chapter_id, role="Рассказчик", verify_state="mismatch")
        db.commit()
        assert audio_integrity.chapter_has_broken_files(db, chapter_id) is True


def test_a_chapter_with_a_missing_file_is_broken():
    """`missing` запирает архив не хуже `mismatch` — оба вердикта означают, что
    сборка соберёт тишину вместо дубля."""
    sessions = _sessions()
    with sessions() as db:
        chapter_id = _chapter(db)
        _take(db, chapter_id, role="Дгарнин", verify_state="ok")
        _take(db, chapter_id, role="Рассказчик", verify_state="missing")
        db.commit()
        assert audio_integrity.chapter_has_broken_files(db, chapter_id) is True


def test_a_chapter_of_only_ok_and_skipped_files_is_not_broken():
    """`skipped` — честное «не считали» (записи старше миграции 0020), а не
    находка: оно не должно запирать архив наравне с mismatch/missing."""
    sessions = _sessions()
    with sessions() as db:
        chapter_id = _chapter(db)
        _take(db, chapter_id, role="Дгарнин", verify_state="ok")
        _take(db, chapter_id, role="Рассказчик", verify_state="skipped")
        db.commit()
        assert audio_integrity.chapter_has_broken_files(db, chapter_id) is False
