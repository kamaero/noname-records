"""Переименовали человека — переименовалось и в его записях.

Имя актёра в `audio_files` — это имя учётки на момент загрузки. Оно замораживается, а
учётку потом переименовывают или сливают, — и файл начинает говорить о человеке,
которого в системе больше нет.

Само по себе это косметика. Но по этому имени решается, дубль перед нами или проба:
роль за тобой — дубль, чужая — проба. Мирон загрузил своего Агга под именем «Der
Grosse», в касте он «Зарецкий Мирон», сверка их не связала — и три записи роли, на
которую он утверждён, легли пробами.

Поэтому имя переносится вместе с учёткой, а вид записи пересчитывается заново.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.audio_uploads as au
from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services.audio_rename import rename_actor_in_audio
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


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(au, "audio_storage", _FakeStorage())
    monkeypatch.setattr(au, "probe_audio_file", lambda path: {})
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        book = ScriptBook(id="b1", title="Крылья полумрака", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive())
        session.add(book)
        session.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=15,
                                  chapter_title="Глава 15", status="published"))
        session.add(Character(book_id="b1", name="Агг", actor_name="Зарецкий Мирон"))
        session.flush()
        yield session


def _upload(db, *, role, actor, chapter="Глава 15"):
    return au.store_audio_file(
        db, payload=b"x", mime_type="audio/wav", original_filename=f"{role}.wav",
        book_code="КП", chapter=chapter, role=role, actor_name=actor,
        kind="audition", safe_name=lambda s: s,
    )


def test_the_name_in_the_files_follows_the_person(db):
    item = _upload(db, role="Агг", actor="Night Owl")
    db.flush()

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert item.actor_name == "Мирон Зарецкий"


def test_the_file_name_is_rebuilt_around_it(db):
    item = _upload(db, role="Агг", actor="Night Owl")
    db.flush()

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert item.canonical_filename == "KP_Ch15_Agg_MironZaretskiy.wav"


def test_a_record_of_his_own_role_becomes_a_take(db):
    """Ради этого всё: три записи роли, на которую он утверждён, лежали пробами."""
    item = _upload(db, role="Агг", actor="Night Owl")
    db.flush()
    assert item.kind == "audition"

    changed = rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert item.kind == "take"
    assert changed == 1


def test_a_record_of_somebody_elses_role_stays_a_probe(db):
    item = _upload(db, role="Сатухух", actor="Night Owl")
    db.flush()

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert item.kind == "audition"
    assert item.canonical_filename.endswith("_proba.wav")


def test_other_people_are_not_touched(db):
    mine = _upload(db, role="Агг", actor="Night Owl")
    other = _upload(db, role="Агг", actor="Кто-то Другой")
    db.flush()

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert mine.actor_name == "Мирон Зарецкий"
    assert other.actor_name == "Кто-то Другой"


def test_renaming_to_the_same_name_changes_nothing(db):
    _upload(db, role="Агг", actor="Мирон Зарецкий")
    db.flush()

    assert rename_actor_in_audio(db, was="Мирон Зарецкий", now="Мирон Зарецкий") == 0


def test_the_upload_time_does_not_move(db):
    """Отметка о событии не меняется оттого, что мы правим строку рядом."""
    item = _upload(db, role="Агг", actor="Night Owl")
    db.flush()
    when = item.uploaded_at

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert item.uploaded_at == when


class TestItHappensByItself:
    """Никто не должен помнить об этом вручную: слияние и переименование — те же места,
    где имя человека меняется, значит там оно и должно догонять его записи."""

    def _people(self, db):
        from app.services.user_admin import create_user

        keep = create_user(db, display_name="Мирон Зарецкий", roles=["dictor"])["user"]
        drop = create_user(db, display_name="Night Owl", roles=["dictor"], login="tg_900000110")["user"]
        return keep, drop

    def test_merging_carries_the_name_into_the_files(self, db):
        from app.services.user_admin import merge_accounts

        keep, drop = self._people(db)
        item = _upload(db, role="Агг", actor="Night Owl")
        db.flush()

        merge_accounts(db, keep_id=keep["id"], drop_id=drop["id"], actor_user_id="кто-то")

        assert item.actor_name == "Мирон Зарецкий"
        assert item.kind == "take", "роль за ним — значит дубль"

    def test_renaming_an_account_does_the_same(self, db):
        from app.services.user_admin import create_user, update_user

        made = create_user(db, display_name="Night Owl", roles=["dictor"])["user"]
        item = _upload(db, role="Агг", actor="Night Owl")
        db.flush()

        update_user(db, made["id"], display_name="Мирон Зарецкий")

        assert item.actor_name == "Мирон Зарецкий"
        assert item.kind == "take"


def test_a_fix_and_the_second_file_keep_their_suffixes(db):
    """Фикс и второй общий файл не сходятся с основным в одно имя после переименования."""
    def take(name):
        return au.store_audio_file(
            db, payload=name.encode(), mime_type="audio/wav", original_filename=name,
            book_code="КП", chapter="Глава 15", role="Агг", actor_name="Night Owl",
            kind="take", safe_name=lambda s: s,
        )

    main, fix, second = take("Agg.wav"), take("Agg_fix1.wav"), take("Agg_again.wav")
    db.flush()
    assert [main.canonical_filename, fix.canonical_filename, second.canonical_filename] == [
        "KP_Ch15_Agg_NightOwl.wav", "KP_Ch15_Agg_NightOwl_fix1.wav", "KP_Ch15_Agg_NightOwl_2.wav",
    ]

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert [main.canonical_filename, fix.canonical_filename, second.canonical_filename] == [
        "KP_Ch15_Agg_MironZaretskiy.wav", "KP_Ch15_Agg_MironZaretskiy_fix1.wav",
        "KP_Ch15_Agg_MironZaretskiy_2.wav",
    ]


def test_merging_into_an_account_with_the_same_file_name_does_not_collide(db):
    mine = au.store_audio_file(
        db, payload=b"a", mime_type="audio/wav", original_filename="Agg.wav", book_code="КП",
        chapter="Глава 15", role="Агг", actor_name="Мирон Зарецкий", kind="take", safe_name=lambda s: s,
    )
    dropped = au.store_audio_file(
        db, payload=b"b", mime_type="audio/wav", original_filename="Agg.wav", book_code="КП",
        chapter="Глава 15", role="Агг", actor_name="Night Owl", kind="take", safe_name=lambda s: s,
    )
    db.flush()

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert mine.canonical_filename == "KP_Ch15_Agg_MironZaretskiy.wav"
    assert dropped.canonical_filename == "KP_Ch15_Agg_MironZaretskiy_2.wav"
