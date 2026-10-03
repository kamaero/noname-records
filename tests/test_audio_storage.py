from __future__ import annotations

from unittest.mock import patch


def test_resolve_path_joins_root_and_key(tmp_path):
    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage
        result = audio_storage.resolve_path("uploads/raw/2024/05/uuid_file.wav")
        assert result == str(tmp_path / "uploads/raw/2024/05/uuid_file.wav")


def test_resolve_path_rejects_traversal(tmp_path):
    import pytest
    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage
        for evil in ("../../etc/passwd", "uploads/../../secret", "../outside.wav"):
            with pytest.raises(ValueError, match="escapes root"):
                audio_storage.resolve_path(evil)
        # Ведущий слэш трактуется относительно корня (безопасно), не падает.
        assert audio_storage.resolve_path("/uploads/raw/x.wav").endswith("uploads/raw/x.wav")


def test_write_file_creates_parent_dirs(tmp_path):
    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage
        key = "uploads/raw/2026/05/test.wav"
        audio_storage.write_file(key, b"audio data")
        assert (tmp_path / key).read_bytes() == b"audio data"


def test_read_file_returns_bytes(tmp_path):
    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage
        key = "uploads/raw/2026/05/test.wav"
        (tmp_path / "uploads/raw/2026/05").mkdir(parents=True)
        (tmp_path / key).write_bytes(b"hello audio")
        assert audio_storage.read_file(key) == b"hello audio"


def test_file_size_returns_correct_bytes(tmp_path):
    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage
        key = "uploads/raw/2026/05/test.wav"
        (tmp_path / "uploads/raw/2026/05").mkdir(parents=True)
        (tmp_path / key).write_bytes(b"x" * 1234)
        assert audio_storage.file_size(key) == 1234


def test_file_exists_true_and_false(tmp_path):
    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage
        key = "uploads/raw/2026/05/test.wav"
        assert audio_storage.file_exists(key) is False
        (tmp_path / "uploads/raw/2026/05").mkdir(parents=True)
        (tmp_path / key).write_bytes(b"data")
        assert audio_storage.file_exists(key) is True


def test_store_audio_file_writes_to_storage(tmp_path):
    """store_audio_file should write bytes via audio_storage, not S3."""
    from unittest.mock import patch
    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services.audio_uploads import store_audio_file
        from app.services import audio_storage

        class _Empty:
            """Подделка запроса: раньше приём дубля в базу не ходил, а теперь ходит —
            считает, какая это по счёту запись роли, чтобы вторая не затёрла первую.
            `.all()` понадобился, когда дубли стали отфильтровывать фиксы в Python
            (см. `next_ordinal`)."""

            def filter(self, *args, **kwargs): return self
            def count(self): return 0
            def all(self): return []
            def first(self): return None

        class _DB:
            def add(self, item): self._item = item
            def query(self, *args, **kwargs): return _Empty()
            def flush(self): pass

        db = _DB()
        result = store_audio_file(
            db,
            payload=b"fake audio bytes",
            mime_type="audio/wav",
            original_filename="test.wav",
            book_code="BOOK",
            chapter="Глава01",
            role="Narrator",
            actor_name="Actor",
            safe_name=lambda x: x,
        )
        # Ключ теперь раскладка по книге/главе (см. audio_mirror.storage_key),
        # а не плоский uuid-путь — старый префикс здесь больше не появляется.
        assert result.stored_key.startswith("BOOK/Chapter01/")
        assert result.stored_key.endswith(".wav")
        assert audio_storage.file_exists(result.stored_key)
        assert audio_storage.read_file(result.stored_key) == b"fake audio bytes"


def test_local_root_raises_if_not_configured():
    # AUDIO_STORAGE_PATH — теперь корень локального диска, а не NAS: приём пишет
    # именно сюда, поэтому без настройки должен падать сразу, а не молча писать в /.
    import app.config
    original = app.config.settings.audio_storage_path
    app.config.settings.audio_storage_path = ""
    try:
        from app.services import audio_storage
        import pytest
        with pytest.raises(RuntimeError, match="AUDIO_STORAGE_PATH"):
            audio_storage.local_root()
    finally:
        app.config.settings.audio_storage_path = original


def test_read_range_returns_only_the_slice_asked_for(tmp_path):
    """Проба весит 80 МБ. Перемотка не должна поднимать её целиком ради тысячи байт."""
    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage
        key = "uploads/raw/2026/09/big.wav"
        (tmp_path / "uploads/raw/2026/09").mkdir(parents=True)
        (tmp_path / key).write_bytes(bytes(range(256)) * 8)

        assert audio_storage.read_range(key, 100, 10) == bytes(range(100, 110))


def test_read_range_stops_at_the_end_of_the_file(tmp_path):
    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage
        key = "uploads/raw/2026/09/small.wav"
        (tmp_path / "uploads/raw/2026/09").mkdir(parents=True)
        (tmp_path / key).write_bytes(b"0123456789")

        assert audio_storage.read_range(key, 7, 100) == b"789"


def test_write_stream_copies_without_holding_the_file_in_memory(tmp_path):
    """Файл Рассказчика — это гигабайт. Прочитать его целиком в память, чтобы тут же
    записать, — значит выбирать между отказом по размеру и падением сервера."""
    import io as _io

    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage

        source = _io.BytesIO(b"a" * (3 * 1024 * 1024))
        written, _digest = audio_storage.write_stream("uploads/raw/2026/09/big.wav", source)

        assert written == 3 * 1024 * 1024
        assert audio_storage.file_size("uploads/raw/2026/09/big.wav") == written


def test_write_stream_creates_the_folders_it_needs(tmp_path):
    import io as _io

    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage

        audio_storage.write_stream("uploads/raw/2027/01/x.wav", _io.BytesIO(b"x"))

        assert (tmp_path / "uploads/raw/2027/01/x.wav").read_bytes() == b"x"


def test_write_stream_returns_the_digest_of_what_it_wrote(tmp_path):
    """Сумму считаем на лету: данные и так проходят через память кусками,
    отдельное чтение файла ради хеша удвоило бы ввод-вывод на гигабайтном дубле."""
    import hashlib
    import io as _io

    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage

        payload = b"x" * (3 * 1024 * 1024 + 17)
        written, digest = audio_storage.write_stream("uploads/raw/2026/09/big.wav", _io.BytesIO(payload))
        assert written == len(payload)
        assert digest == hashlib.md5(payload).hexdigest()


def test_write_file_returns_the_digest_too(tmp_path):
    import hashlib

    with patch("app.services.audio_storage.local_root", return_value=str(tmp_path)):
        from app.services import audio_storage

        digest = audio_storage.write_file("uploads/raw/2026/09/small.wav", b"audio data")
        assert digest == hashlib.md5(b"audio data").hexdigest()


def test_the_same_key_addresses_both_copies(tmp_path, monkeypatch):
    """Ключ один на обе копии: зеркалированию нечего пересчитывать, а человек
    видит одинаковые папки на сервере и на NAS."""
    import io as _io

    from app.services import audio_storage

    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", str(tmp_path / "nas"))
    key = "KP/Chapter05/KP_Ch05_Gamuk_GoncharovIvan.wav"

    audio_storage.write_stream(key, _io.BytesIO(b"bytes"))
    assert (tmp_path / "rec" / key).exists()
    assert not (tmp_path / "nas" / key).exists()

    audio_storage.write_stream(key, _io.BytesIO(b"bytes"), location="nas")
    assert (tmp_path / "nas" / key).exists()
    assert audio_storage.resolve_path(key) == str(tmp_path / "rec" / key)
    assert audio_storage.resolve_path(key, location="nas") == str(tmp_path / "nas" / key)


def test_an_unconfigured_nas_root_cannot_address_anything(tmp_path, monkeypatch):
    """Отсутствие зеркала — законное состояние: приём работает, второй копии просто
    не появляется. Но адресовать в несуществующем корне нечего, и молчаливый путь
    от рабочего каталога процесса — это запись куда попало вместо честного отказа."""
    import pytest

    from app.services import audio_storage

    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", "")

    assert audio_storage.resolve_path("KP/Chapter01/a.wav") == str(tmp_path / "rec" / "KP/Chapter01/a.wav")
    with pytest.raises(RuntimeError):
        audio_storage.resolve_path("KP/Chapter01/a.wav", location="nas")
