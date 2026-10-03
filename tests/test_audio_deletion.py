"""Операция удаления записи: сначала след в журнале, потом разрушение.

Владельцу и админу — любой файл. Диктору — только свой и только в течение
суток после загрузки; «свой» проверяется тем же `names_match`, каким студия
уже отвечает на вопрос «моя ли это роль».
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import session_serializer
from app.db import Base
from app.db import SessionLocal as AppSessionLocal
from app.db import engine as app_engine
from app.main import app
from app.models import AsrJob, AudioFile, OperatorIntervention, PendingMirrorDeletion, ScriptBook
from app.services import audio_storage
from app.services.audio_deletion import AudioDeleteError, delete_audio_file
from app.time_utils import utcnow_naive, iso_utc

BOOK = "book-1"
# «Крылья Полумрака» -> инициалы двух слов -> «КП».
BOOK_CODE = "КП"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья Полумрака",
                               source_filename="k.txt", source_format="txt"))
        yield session


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_storage, "local_root", lambda: str(tmp_path))
    return tmp_path


def _add_audio(db, *, role: str, actor_name: str, days_old: int, kind: str = "take",
               chapter: str = "Глава 1", book_code: str = BOOK_CODE) -> AudioFile:
    audio = AudioFile(
        id=str(uuid.uuid4()),
        book_code=book_code,
        original_filename="запись.wav",
        stored_key=f"uploads/{uuid.uuid4()}.wav",
        mime_type="audio/wav",
        size_bytes=12345,
        chapter=chapter,
        role=role,
        actor_name=actor_name,
        canonical_filename=f"{book_code}_Ch01_{role}.wav",
        kind=kind,
        location="local",
        uploaded_at=utcnow_naive() - timedelta(days=days_old),
    )
    db.add(audio)
    db.flush()
    audio_storage.write_file(audio.stored_key, b"audio-bytes")
    return audio


def test_владелец_удаляет_любую_запись(db, storage):
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=90)
    db.commit()

    result = delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    assert result["role"] == "Гамук"
    assert db.get(AudioFile, audio.id) is None


def test_диктор_удаляет_свою_свежую_запись(db, storage):
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="u1", actor_name="Гончаров Иван", privileged=False)
    db.commit()

    assert db.get(AudioFile, audio.id) is None


def test_диктор_не_удаляет_чужую_запись(db, storage):
    audio = _add_audio(db, role="Тупуг", actor_name="Сомов Роман", days_old=0)
    db.commit()

    with pytest.raises(AudioDeleteError) as caught:
        delete_audio_file(db, audio.id, actor_uid="u1", actor_name="Гончаров Иван", privileged=False)
    assert caught.value.code == "forbidden"
    assert db.get(AudioFile, audio.id) is not None


def test_диктор_не_удаляет_вчерашнюю_запись(db, storage):
    """Сутки, а не бесконечность: старую запись уже могли пустить в дело."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=2)
    db.commit()

    with pytest.raises(AudioDeleteError) as caught:
        delete_audio_file(db, audio.id, actor_uid="u1", actor_name="Гончаров Иван", privileged=False)
    assert caught.value.code == "too_old"


def test_журнал_называет_роль_ключ_хранения_и_имя_файла(db, storage):
    """Строки в базе не останется — журнал единственный след, и вот что в нём.

    Именно состав payload, а не порядок шагов: порядок «журнал раньше
    разрушения» стерегут `test_журнал_переживает_падение_на_втором_коммите` и
    `test_сбой_между_коммитами_не_рвёт_согласованность_через_ручку` — они
    инсценируют сбой на стыке, а этот тест на перестановку шагов не среагировал
    бы вовсе."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    key = audio.stored_key
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    row = db.query(OperatorIntervention).filter(OperatorIntervention.action_type == "audio_delete").one()
    payload = json.loads(row.payload_json)
    assert payload["role"] == "Гамук"
    assert payload["stored_key"] == key
    assert payload["canonical_filename"]


def test_журнал_называет_диктора_и_время_загрузки_отдельно_от_удалившего(db, storage):
    """После коммита строки `audio_files` не останется — payload единственный
    след того, ЧЬЯ была запись. Раньше это можно было только угадать разбором
    `canonical_filename` по соглашению об именовании; теперь это два явных поля.

    Удаляет владелец, а запись — дикторская: имена обязаны разойтись, иначе
    тест прошёл бы и на совпадении, ничего не доказав."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    uploaded_at = audio.uploaded_at
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    row = db.query(OperatorIntervention).filter(OperatorIntervention.action_type == "audio_delete").one()
    payload = json.loads(row.payload_json)
    assert payload["uploaded_by"] == "Гончаров Иван"
    assert payload["uploaded_at"] == iso_utc(uploaded_at)
    # Собственно точка отказа старого способа: "кто удалил" и "чья запись" —
    # разные имена, и оба видны, а не только одно из двух.
    assert row.actor_name == "Владелец"
    assert payload["uploaded_by"] != row.actor_name


def test_журнал_называет_саму_запись_и_код_книги(db, storage):
    """Строки `audio_files` после коммита не останется, а `book_id` пуст всегда,
    когда книга по коду не нашлась — ветка допустимая и нарочно поддержанная.
    Без `audio_id` связать удалённое с артефактом, где этот идентификатор
    записан (задание распознавания, выгрузка, переписка), нечем вовсе, а без
    `book_code` про книгу остаётся только заголовок главы строкой.

    Довод тот же, по которому здесь уже появились имя диктора и время загрузки:
    правка аддитивная, а уже удалённым записям эти поля задним числом не
    проставишь — либо они пишутся сейчас, либо этих следов не будет никогда."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0, book_code="ЖЖ")
    audio_id = audio.id
    db.commit()

    delete_audio_file(db, audio_id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    row = db.query(OperatorIntervention).filter(OperatorIntervention.action_type == "audio_delete").one()
    # Книга не нашлась — и это ровно тот случай, ради которого поля и добавлены.
    assert row.book_id == ""
    payload = json.loads(row.payload_json)
    assert payload["audio_id"] == audio_id
    assert payload["book_code"] == "ЖЖ"


def test_журнал_называет_сколько_заданий_распознавания_ушло(db, storage):
    """Задания уносятся вместе с записью и молча: их удаление не оставляет
    собственного следа нигде. Число в payload — единственное, что потом скажет,
    была ли у записи расшифровка и сколько попыток за ней стояло."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    db.add(AsrJob(id="job-1", audio_file_id=audio.id, chapter_id="ch-1", status="done"))
    db.add(AsrJob(id="job-2", audio_file_id=audio.id, chapter_id="ch-1", status="failed"))
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    row = db.query(OperatorIntervention).filter(OperatorIntervention.action_type == "audio_delete").one()
    assert json.loads(row.payload_json)["asr_jobs_deleted"] == 2


def test_локальный_файл_стирается(db, storage):
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    path = audio_storage.resolve_path(audio.stored_key)
    db.commit()
    assert os.path.isfile(path)

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    assert not os.path.isfile(path)


def test_поручение_зеркалу_остаётся(db, storage):
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    key = audio.stored_key
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    assert [item.stored_key for item in db.query(PendingMirrorDeletion).all()] == [key]


def test_задания_распознавания_уходят_вместе_с_файлом(db, storage):
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    db.add(AsrJob(id="job-1", audio_file_id=audio.id, chapter_id="ch-1", status="done"))
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    assert db.query(AsrJob).filter(AsrJob.audio_file_id == audio.id).count() == 0


def test_пропавший_с_диска_файл_удаляется_до_конца(db, storage):
    """Цель — чтобы система перестала считать запись существующей."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    os.remove(audio_storage.resolve_path(audio.stored_key))
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    assert db.get(AudioFile, audio.id) is None
    assert db.query(PendingMirrorDeletion).count() == 1


def test_неудаливший_файл_не_рушит_операцию(db, storage):
    """К моменту `os.remove` база уже согласованна необратимо (оба коммита
    позади) — файл, который не удалось стереть по любой причине (не только
    `FileNotFoundError`), не должен превращать успешную операцию в исключение.
    `PendingMirrorDeletion` в любом случае даёт фоновому циклу ещё одну попытку."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    db.commit()

    with patch("app.services.audio_deletion.os.remove", side_effect=PermissionError("нет прав")):
        result = delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)

    assert result["role"] == "Гамук"
    assert db.get(AudioFile, audio.id) is None
    assert db.query(PendingMirrorDeletion).filter_by(stored_key=audio.stored_key).count() == 1


def test_чужая_старая_запись_даёт_forbidden_а_не_too_old(db, storage):
    """Приоритет отказов: «чужая» важнее «старой». Диктору незачем ждать —
    время само по себе ничего не изменит, запись всё равно не его."""
    audio = _add_audio(db, role="Тупуг", actor_name="Сомов Роман", days_old=2)
    db.commit()

    with pytest.raises(AudioDeleteError) as caught:
        delete_audio_file(db, audio.id, actor_uid="u1", actor_name="Гончаров Иван", privileged=False)
    assert caught.value.code == "forbidden"
    assert db.get(AudioFile, audio.id) is not None


def test_запись_без_книги_всё_равно_журналируется(db, storage):
    """`book_code`, не соответствующий ни одной книге, не должен глушить след —
    это как раз та ветка, что защищает журнал как единственное свидетельство."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0, book_code="ЖЖ")
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    db.commit()

    row = db.query(OperatorIntervention).filter(OperatorIntervention.action_type == "audio_delete").one()
    assert row.book_id == ""
    payload = json.loads(row.payload_json)
    assert payload["role"] == "Гамук"
    assert db.get(AudioFile, audio.id) is None


def test_агент_не_удаляет_даже_свою_свежую_загрузку(db, storage):
    """Тот самый случай, где без правила отказа бы не было.

    Файл, загруженный агентом за Ивана Петрова, подписан Иваном Петровым, и
    `names_match` не совпадёт сам собой. А вот собственную загрузку агент стёр бы.
    """
    audio = _add_audio(db, role="Химера", actor_name="Агата Ковалёва", days_old=0)
    db.commit()

    with pytest.raises(AudioDeleteError) as caught:
        delete_audio_file(db, audio.id, actor_uid="agata", actor_name="Агата Ковалёва",
                          privileged=False, is_agent=True)

    assert caught.value.code == "forbidden_agent"


def test_отказ_агенту_ничего_не_разрушает(db, storage):
    audio = _add_audio(db, role="Химера", actor_name="Агата Ковалёва", days_old=0)
    db.commit()

    with pytest.raises(AudioDeleteError):
        delete_audio_file(db, audio.id, actor_uid="agata", actor_name="Агата Ковалёва",
                          privileged=False, is_agent=True)

    assert db.get(AudioFile, audio.id) is not None
    assert db.query(PendingMirrorDeletion).count() == 0


def test_агент_не_удаляет_и_чужую_свежую(db, storage):
    audio = _add_audio(db, role="Химера", actor_name="Натали Ким", days_old=0)
    db.commit()

    with pytest.raises(AudioDeleteError) as caught:
        delete_audio_file(db, audio.id, actor_uid="agata", actor_name="Агата Ковалёва",
                          privileged=False, is_agent=True)

    assert caught.value.code == "forbidden_agent"


def test_запрет_агенту_не_задел_диктора(db, storage):
    """Окно в сутки заводили для того, кто записывал, — оно должно остаться."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="u1", actor_name="Гончаров Иван",
                      privileged=False, is_agent=False)
    db.commit()

    assert db.get(AudioFile, audio.id) is None


def test_владелец_с_ролью_агента_удаляет_как_прежде(db, storage):
    """Совмещённая роль ничего не отнимает: `privileged` перевешивает."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=90)
    db.commit()

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец",
                      privileged=True, is_agent=True)
    db.commit()

    assert db.get(AudioFile, audio.id) is None


def test_журнал_переживает_падение_на_втором_коммите(db, storage):
    """Сбой теперь инсценируем на настоящем стыке между двумя коммитами внутри
    `delete_audio_file`: первый (журнал) уже состоялся, второй (строка +
    задания распознавания + поручение зеркалу) — нет. `os.remove` к этому
    моменту не звался вовсе: он идёт последним шагом, уже после обоих коммитов,
    поэтому файл остаётся цел, а не просто «удаление не отменяется».

    Старая точка сбоя (после `os.remove`) с этой правки недостижима в принципе:
    `except OSError` внутри функции больше не даёт сбою `os.remove` вылететь
    наружу вообще — см. `test_неудаливший_файл_не_рушит_операцию` ниже."""
    audio = _add_audio(db, role="Гамук", actor_name="Гончаров Иван", days_old=0)
    path = audio_storage.resolve_path(audio.stored_key)
    db.commit()

    original_commit = db.commit
    calls = {"n": 0}

    def flaky_commit():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("база оборвалась между двумя коммитами")
        return original_commit()

    db.commit = flaky_commit
    try:
        with pytest.raises(RuntimeError):
            delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)
    finally:
        db.commit = original_commit

    assert calls["n"] == 2  # сбой пришёлся ровно на второй коммит, не на первый

    # Симулируем падение процесса после сбойного второго коммита: всё, что не
    # было закоммичено, теряется.
    db.rollback()

    row = db.query(OperatorIntervention).filter(OperatorIntervention.action_type == "audio_delete").one()
    payload = json.loads(row.payload_json)
    assert payload["role"] == "Гамук"
    # Строка audio_files тоже цела: второй коммит не состоялся, откатилось
    # вместе — строка, задания распознавания, поручение зеркалу.
    assert db.get(AudioFile, audio.id) is not None
    assert db.query(PendingMirrorDeletion).count() == 0
    # Файл на диске цел: `os.remove` — последний шаг, до него дело не дошло.
    assert os.path.isfile(path)


# --- ручка: POST /api/recording/files/{audio_id}/delete -----------------------
#
# Клиент и вход в сессию — тем же способом, что и в `tests/test_recording_access.py`:
# `SessionLocal.configure(bind=...)` на in-memory sqlite со `StaticPool` (TestClient
# гоняет ручку в отдельном потоке) плюс `session_serializer` в куке.

SELF_ACTOR = "Гончаров Иван"
OTHER_ACTOR = "Сомов Роман"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_storage, "local_root", lambda: str(tmp_path))
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    AppSessionLocal.configure(bind=engine)
    with sessionmaker(bind=engine)() as seed:
        seed.add(ScriptBook(id=BOOK, title="Крылья Полумрака", source_filename="k.txt", source_format="txt"))
        seed.commit()
    try:
        yield TestClient(app)
    finally:
        AppSessionLocal.configure(bind=app_engine)


def _with_session(client, *, roles, display_name):
    token = session_serializer.dumps({"uid": "u1", "sub": "u1", "roles": list(roles), "display_name": display_name})
    client.cookies.set("session", token)
    return client


@pytest.fixture()
def client_as_dictor(client):
    return _with_session(client, roles=["dictor"], display_name=SELF_ACTOR)


@pytest.fixture()
def client_as_owner(client):
    return _with_session(client, roles=["admin"], display_name="Владелец")


def _seed_audio(**kwargs) -> AudioFile:
    with AppSessionLocal() as db:
        audio = _add_audio(db, **kwargs)
        db.commit()
        db.refresh(audio)
        db.expunge(audio)
    return audio


@pytest.fixture()
def foreign_audio(client):
    return _seed_audio(role="Тупуг", actor_name=OTHER_ACTOR, days_old=0)


@pytest.fixture()
def own_audio(client):
    return _seed_audio(role="Гамук", actor_name=SELF_ACTOR, days_old=0)


def test_ручка_удаления_требует_сессии(client):
    assert client.post("/api/recording/files/whatever/delete").status_code == 401


def test_ручка_отвечает_объяснением_а_не_кодом(client_as_dictor, foreign_audio):
    response = client_as_dictor.post(f"/api/recording/files/{foreign_audio.id}/delete")
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    # Именно словами, а не голым кодом: тот же обычай, что у `RENAME_ERRORS` на фронте.
    assert response.json()["error"] != response.json()["message"]
    assert "чуж" in response.json()["message"].lower()


def test_ручка_не_находит_чужого_идентификатора(client_as_dictor):
    response = client_as_dictor.post("/api/recording/files/нет-такой/delete")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert "message" in response.json() and response.json()["message"]


def test_ручка_отказывает_в_старой_записи_словами(client_as_dictor):
    old_audio = _seed_audio(role="Гамук", actor_name=SELF_ACTOR, days_old=2)
    response = client_as_dictor.post(f"/api/recording/files/{old_audio.id}/delete")
    assert response.status_code == 403
    assert response.json()["error"] == "too_old"
    assert "суток" in response.json()["message"].lower()


def test_диктор_удаляет_свою_запись_через_ручку(client_as_dictor, own_audio):
    path = audio_storage.resolve_path(own_audio.stored_key)
    assert os.path.isfile(path)

    response = client_as_dictor.post(f"/api/recording/files/{own_audio.id}/delete")
    assert response.status_code == 200
    assert response.json()["ok"] is True

    assert not os.path.isfile(path)
    with AppSessionLocal() as db:
        assert db.get(AudioFile, own_audio.id) is None
        assert db.query(PendingMirrorDeletion).filter_by(stored_key=own_audio.stored_key).count() == 1


def test_владелец_удаляет_чужую_запись_через_ручку(client_as_owner, foreign_audio):
    response = client_as_owner.post(f"/api/recording/files/{foreign_audio.id}/delete")
    assert response.status_code == 200
    with AppSessionLocal() as db:
        assert db.get(AudioFile, foreign_audio.id) is None


def test_сбой_между_коммитами_не_рвёт_согласованность_через_ручку(client_as_owner, own_audio, monkeypatch):
    """Раньше здесь демонстрировали дыру: коммит журнала (внутри
    `delete_audio_file`) и коммит хвоста жили в разных функциях — сервисе и
    ручке, — а между ними успевал состояться необратимый `os.remove`. Ревью
    заметило верно: пока шов проходит между файлами, окно можно сузить, но не
    закрыть. Теперь оба коммита — внутри `delete_audio_file`, `os.remove` идёт
    последним, а ручка вообще не коммитит, так что развести их больше нельзя.

    Этот тест был тем самым тестом на противоречие; теперь он — сторож: тот же
    сбой на втором (по счёту во всём запросе) коммите `Session.commit()`
    оставляет систему согласованной — просто операция не завершилась. Файл
    цел, строка цела, поручения зеркалу нет — можно повторить попытку заново.
    """
    path = audio_storage.resolve_path(own_audio.stored_key)
    assert os.path.isfile(path)

    original_commit = Session.commit
    calls = {"n": 0}

    def flaky_commit(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("база оборвалась между двумя коммитами")
        return original_commit(self, *a, **kw)

    monkeypatch.setattr(Session, "commit", flaky_commit)
    try:
        with pytest.raises(RuntimeError):
            client_as_owner.post(f"/api/recording/files/{own_audio.id}/delete")
    finally:
        monkeypatch.setattr(Session, "commit", original_commit)

    assert calls["n"] == 2  # сбой пришёлся на второй коммит, оба — внутри delete_audio_file

    # os.remove — последний шаг, после обоих коммитов: до него дело не дошло.
    assert os.path.isfile(path)
    with AppSessionLocal() as check:
        # Журнал переживает падение — он закоммичен первым и отдельно.
        row = check.query(OperatorIntervention).filter(OperatorIntervention.action_type == "audio_delete").one()
        assert json.loads(row.payload_json)["canonical_filename"] == own_audio.canonical_filename
        # А второй коммит не состоялся — откатилось всё, что он должен был
        # зафиксировать: строка audio_files цела, поручения зеркалу нет.
        assert check.get(AudioFile, own_audio.id) is not None
        assert check.query(PendingMirrorDeletion).filter_by(stored_key=own_audio.stored_key).count() == 0


def test_удалённая_проба_уносит_свой_дизлайк_и_отменяет_отказ(db, storage):
    """Иначе 👎 удалённой пробы держал бы отказ, и письмо ушло бы про пробу, которой нет."""
    from app.models import AuditionReaction, AuditionRejection
    from app.services.audition_reactions import set_reaction

    audio = _add_audio(db, role="Вукьрадух", actor_name="Роман Сомов", days_old=0, kind="audition", chapter="")
    db.commit()
    set_reaction(db, audio.id, voter_uid="author-1", voter_name="Автор", value=-1)

    delete_audio_file(db, audio.id, actor_uid="owner", actor_name="Владелец", privileged=True)

    assert db.query(AuditionReaction).count() == 0
    assert db.query(AuditionRejection).one().status == "cancelled"
