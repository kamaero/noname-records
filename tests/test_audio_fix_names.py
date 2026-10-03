"""Дозапись: фикс называется по-своему, второй общий файл получает номер, ничего не затирается."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base


@pytest.mark.parametrize("name, expected", [
    ("KP_Ch01_Gamuk_fix1.wav", 1),
    ("KP_Ch01_Gamuk_Goncharov_FIX12.wav", 12),
    ("KP Ch01 Gamuk фикс2.wav", 2),
    ("KP_Ch01_Gamuk_fix.wav", 0),
    ("KP_Ch01_Gamuk_Goncharov.wav", None),
    ("KP_Ch01_Prefix1_Gamuk.wav", None),
    ("KP_Ch01_Gamuk_fixture.wav", None),
])
def test_fix_number(name, expected):
    from app.services.audio_uploads import fix_number
    assert fix_number(name) == expected


def test_canonical_name_with_fix_and_ordinal():
    from app.services.audio_naming import canonical_audio_name
    base = dict(book_code="KP", chapter="Глава 11", role="Гамук", actor_name="Гончаров Иван", original_filename="x.wav")
    plain = canonical_audio_name(**base)
    assert canonical_audio_name(**base, fix=1) == plain[:-4] + "_fix1.wav"
    assert canonical_audio_name(**base, ordinal=2) == plain[:-4] + "_2.wav"


def test_fix_token_does_not_pollute_role_or_actor():
    from app.services.audio_uploads import parse_batch_audio_filename
    parsed = parse_batch_audio_filename("KP_Ch01_Gamuk_fix1.wav", default_book_code="KP",
                                        default_actor_name="", normalize_role_label=lambda s: s)
    assert parsed["role"] == "Gamuk"
    assert "fix" not in parsed["actor_name"].lower()
    assert parsed["canonical_filename"].endswith("_fix1.wav")


@pytest.fixture()
def db(tmp_path, monkeypatch):
    import app.models  # noqa: F401
    from app.services import audio_storage
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def _store(db, name, payload):
    import io
    from app.services.audio_uploads import store_audio_file
    # Брифовый вызов без `safe_name` не соответствует реальной сигнатуре
    # (`safe_name` — обязательный keyword, см. app/services/audio_uploads.py и
    # app/handler_factories.py:store_audio_file, который его подставляет через DI).
    # Тестовые данные минимально дополнены: safe_name=lambda s: s, как в
    # tests/test_audition_batch_endpoint.py и других тестах store_audio_file.
    return store_audio_file(db, source=io.BytesIO(payload), mime_type="audio/wav", original_filename=name,
                            book_code="KP", chapter="Глава 11", role="Гамук", actor_name="Гончаров Иван",
                            safe_name=lambda s: s)


def test_second_full_file_fix_and_taken_fix_number(db, monkeypatch):
    from app.services import audio_uploads
    monkeypatch.setattr(audio_uploads, "probe_audio_file", lambda path: {})
    first = _store(db, "KP_Ch11_Gamuk.wav", b"A")
    fix = _store(db, "KP_Ch11_Gamuk_fix1.wav", b"B")
    fix_again = _store(db, "KP_Ch11_Gamuk_fix1.wav", b"C")
    bare_fix = _store(db, "KP_Ch11_Gamuk_fix.wav", b"D")
    second = _store(db, "KP_Ch11_Gamuk_Goncharov.wav", b"E")

    names = [first.canonical_filename, fix.canonical_filename, fix_again.canonical_filename,
             bare_fix.canonical_filename, second.canonical_filename]
    stem = first.canonical_filename[:-4]
    assert names == [f"{stem}.wav", f"{stem}_fix1.wav", f"{stem}_fix2.wav", f"{stem}_fix3.wav", f"{stem}_2.wav"]
    assert len({item.stored_key for item in [first, fix, fix_again, bare_fix, second]}) == 5


def test_deleting_the_first_file_does_not_let_the_next_one_overwrite_the_second(db, monkeypatch):
    """`base`, `_2`; актёр удаляет `base` — следующий общий файл не должен получить `_2` снова."""
    from app.services import audio_storage, audio_uploads
    monkeypatch.setattr(audio_uploads, "probe_audio_file", lambda path: {})
    first = _store(db, "KP_Ch11_Gamuk.wav", b"FIRST")
    second = _store(db, "KP_Ch11_Gamuk.wav", b"SECOND")
    second_key = second.stored_key
    db.delete(first)
    db.flush()

    third = _store(db, "KP_Ch11_Gamuk.wav", b"THIRD")

    keys = {first.stored_key, second_key, third.stored_key}
    assert len(keys) == 3
    assert third.canonical_filename.endswith("_3.wav")
    assert audio_storage.read_file(second_key) == b"SECOND"


def test_an_orphan_file_on_disk_is_not_overwritten(db, monkeypatch):
    """Файл лежит на диске без строки в базе — номер из базы свободен, но ключ занят."""
    from app.services import audio_mirror, audio_storage, audio_uploads
    monkeypatch.setattr(audio_uploads, "probe_audio_file", lambda path: {})
    stem = audio_uploads.build_canonical_audio_filename("KP", "Глава 11", "Гамук", "Гончаров Иван", "x.wav")
    orphan_key = audio_mirror.storage_key("KP", "Глава 11", stem, "take")
    audio_storage.write_file(orphan_key, b"ORPHAN")
    fix_key = audio_mirror.storage_key("KP", "Глава 11", stem[:-4] + "_fix1.wav", "take")
    audio_storage.write_file(fix_key, b"ORPHAN-FIX")

    full = _store(db, "KP_Ch11_Gamuk.wav", b"NEW")
    fix = _store(db, "KP_Ch11_Gamuk_fix1.wav", b"NEWFIX")

    assert full.stored_key != orphan_key and full.canonical_filename.endswith("_2.wav")
    assert fix.stored_key != fix_key and fix.canonical_filename.endswith("_fix2.wav")
    assert audio_storage.read_file(orphan_key) == b"ORPHAN"
    assert audio_storage.read_file(fix_key) == b"ORPHAN-FIX"


def test_a_long_name_keeps_its_number_in_the_storage_key(db, monkeypatch):
    """Основа имени длиннее 120 символов: `safe_name` не должен съесть `_2` и расширение."""
    import io
    from app.services import audio_uploads
    from app.services.shared_runtime import safe_name
    monkeypatch.setattr(audio_uploads, "probe_audio_file", lambda path: {})
    role = "Очень Длинное Имя Роли " * 6
    actor = "Невероятно Длинное Имя Актёра " * 4

    def store(payload):
        return audio_uploads.store_audio_file(
            db, source=io.BytesIO(payload), mime_type="audio/wav", original_filename="x.wav",
            book_code="KP", chapter="Глава 11", role=role, actor_name=actor, safe_name=safe_name,
        )

    first, second = store(b"A"), store(b"B")
    fix = audio_uploads.store_audio_file(
        db, source=io.BytesIO(b"C"), mime_type="audio/wav", original_filename="x_fix1.wav",
        book_code="KP", chapter="Глава 11", role=role, actor_name=actor, safe_name=safe_name,
    )

    assert len(first.canonical_filename) > 124
    assert len({first.stored_key, second.stored_key, fix.stored_key}) == 3
    assert second.stored_key.endswith("_2.wav") and fix.stored_key.endswith("_fix1.wav")
    assert second.canonical_filename.endswith("_2.wav")


def test_the_key_search_fails_loudly_instead_of_hanging(db, monkeypatch):
    import io
    from app.services import audio_uploads
    monkeypatch.setattr(audio_uploads, "probe_audio_file", lambda path: {})
    monkeypatch.setattr(audio_uploads, "_storage_key_taken", lambda db, key: True)
    with pytest.raises(RuntimeError, match="storage_key_exhausted"):
        audio_uploads.store_audio_file(
            db, source=io.BytesIO(b"A"), mime_type="audio/wav", original_filename="x.wav",
            book_code="KP", chapter="Глава 11", role="Гамук", actor_name="Гончаров Иван",
            safe_name=lambda s: s,
        )


@pytest.mark.parametrize("canonical", [
    "KP_Ch11_Gamuk_GoncharovIvan.wav", "KP_Ch11_Gamuk_GoncharovIvan_2.wav",
    "KP_Ch11_Gamuk_GoncharovIvan_fix3.wav", "KP_Gamuk_Ivan_proba_2.wav",
])
def test_short_names_keep_the_same_storage_name(canonical):
    from app.services.audio_uploads import storage_file_name
    from app.services.shared_runtime import safe_name
    assert storage_file_name(canonical, safe_name) == safe_name(canonical)
