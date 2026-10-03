"""Переименовали актёра — переехал и файл на диске.

Раньше менялось только каноническое имя в базе: в папке главы файл лежал под прежним
именем, и владелец, открыв папку на NAS, видел одного человека, а сайт — другого. Второй
раз это кусалось при переезде раскладки: новый ключ ещё не переехавшей строки целил в
файл, который переехал под этим именем раньше.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.audio_uploads as au
from app.config import settings
from app.db import Base
from app.models import AudioFile, Character, PendingMirrorDeletion, ScriptBook, ScriptChapter
from app.services.audio_rename import rename_actor_in_audio
from app.services.shared_runtime import safe_name
from app.time_utils import utcnow_naive


@pytest.fixture()
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "audio_storage_path", str(tmp_path / "local"))
    monkeypatch.setattr(settings, "audio_nas_path", str(tmp_path / "nas"))
    monkeypatch.setattr(au, "probe_audio_file", lambda path: {})
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        session.add(ScriptBook(id="b1", title="Крылья полумрака", source_filename="k.txt",
                               source_format="txt", created_at=utcnow_naive()))
        session.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=15,
                                  chapter_title="Глава 15", status="published"))
        session.add(Character(book_id="b1", name="Агг", actor_name="Зарецкий Мирон"))
        session.flush()
        yield session


def _upload(db, *, role="Агг", actor="Night Owl"):
    item = au.store_audio_file(
        db, payload=b"wav-bytes", mime_type="audio/wav", original_filename=f"{role}.wav",
        book_code="КП", chapter="Глава 15", role=role, actor_name=actor,
        kind="take", safe_name=safe_name,
    )
    db.flush()
    return item


def _local(key):
    return os.path.join(settings.audio_storage_path, key)


def test_the_file_on_disk_takes_the_new_name(db):
    item = _upload(db)
    old_key = item.stored_key

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert item.stored_key.endswith(item.canonical_filename)
    assert item.stored_key != old_key
    assert os.path.isfile(_local(item.stored_key))
    assert not os.path.exists(_local(old_key))
    with open(_local(item.stored_key), "rb") as moved:
        assert moved.read() == b"wav-bytes"


def test_the_mirror_copy_is_redone_under_the_new_name_and_the_old_one_is_queued_for_removal(db):
    item = _upload(db)
    old_key = item.stored_key
    item.mirrored_at = utcnow_naive()
    item.mirror_state = "ok"
    db.flush()

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert item.mirrored_at is None, "копии на NAS под новым именем ещё нет — зеркало должно её сделать"
    assert [row.stored_key for row in db.query(PendingMirrorDeletion).all()] == [old_key]


def test_an_unmirrored_file_leaves_nothing_to_remove_on_the_nas(db):
    _upload(db)

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert db.query(PendingMirrorDeletion).count() == 0


def test_a_rollback_puts_the_file_back(db):
    """Имя в базе откатилось — файл обязан вернуться, иначе строка укажет в пустоту."""
    item = _upload(db)
    db.commit()
    old_key = item.stored_key

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")
    db.rollback()

    row = db.get(AudioFile, item.id)
    assert row.stored_key == old_key
    assert os.path.isfile(_local(old_key))


def test_a_commit_keeps_the_file_where_it_moved(db):
    item = _upload(db)
    db.commit()

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")
    db.commit()
    db.rollback()  # позднейший откат другой работы не должен дёргать уже закоммиченный переезд

    assert os.path.isfile(_local(db.get(AudioFile, item.id).stored_key))


def test_a_file_already_lying_under_the_new_name_is_not_overwritten(db):
    item = _upload(db)
    old_key = item.stored_key
    stranger = _local(old_key.replace("NightOwl", "MironZaretskiy"))
    os.makedirs(os.path.dirname(stranger), exist_ok=True)
    with open(stranger, "wb") as handle:
        handle.write(b"somebody else")

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert item.stored_key == old_key, "чужой файл на месте — переезд отменяется, строка указывает на свой"
    with open(stranger, "rb") as handle:
        assert handle.read() == b"somebody else"


def test_a_legacy_nas_only_file_stays_put(db):
    """Единственная копия на NAS: трогать её из обработчика запроса нельзя."""
    item = _upload(db)
    item.location = "nas"
    old_key = item.stored_key
    db.flush()

    rename_actor_in_audio(db, was="Night Owl", now="Мирон Зарецкий")

    assert item.stored_key == old_key
    assert item.canonical_filename.endswith("MironZaretskiy.wav")
