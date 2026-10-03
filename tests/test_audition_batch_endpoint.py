"""Загрузка проб идёт тем же маршрутом, что и дубли, и подчиняется другим правилам.

Что именно пришло, решает сервер: роль за диктором — дубль, чужая роль — проба. Форма
об этом не спрашивает, потому что дикторы отвечали неверно, и это не их вина — помнить,
кто на что утверждён, обязана система.

Дубль главы один: второй файл на то же место — почти всегда промах, и его ловит
проверка на дубликат. Попыток на роль столько, сколько актёр захочет: та же проверка
на пробах запрещала бы прислать вторую, лучшую.
"""
import asyncio
import io
import json

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.audio_uploads as au
from app.api.recording import build_recording_handlers
from app.db import Base
from app.models import AudioFile, Character, ScriptBook
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


class _Request:
    headers: dict = {}


def _sessions(approved_role: str = ""):
    """`approved_role` — роль, на которую Роман Сомов утверждён в касте."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    if approved_role:
        with sessions() as db:
            book = ScriptBook(title="Крылья полумрака", source_filename="k.txt", source_format="txt", created_at=utcnow_naive())
            db.add(book)
            db.flush()
            db.add(Character(book_id=book.id, name=approved_role, actor_name="Роман Сомов"))
            db.commit()
    return sessions


def _upload(name: str) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(b"RIFF0000"))


def _handlers(sessions, sent: list):
    return build_recording_handlers({
        "is_authenticated": lambda request: True,
        "has_any_role": lambda request, roles: True,
        "has_workspace_full_access": lambda request: True,
        "session_payload": lambda request: {"display_name": "Роман Сомов", "sub": "tg_1"},
        "is_agent": lambda request: False,
        "SessionLocal": sessions,
        "AudioFile": AudioFile,
        "build_canonical_audio_filename": au.build_canonical_audio_filename,
        "store_audio_file": lambda db, **kwargs: au.store_audio_file(db, safe_name=lambda s: s, **kwargs),
        "send_telegram_message": lambda db, text: sent.append(text),
        "recording_identity": lambda request, name: ("u1", name),
        "mark_role_progress_recorded": lambda *args: 0,
        "recording_workspace_payload": lambda db, **kwargs: {},
        "require_recording_access": lambda request: None,
        "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": {
            "filename_chapter_index": 0,
        },
    })


def _post(files, meta, approved_role=""):
    sessions = _sessions(approved_role)
    sent: list = []
    original, au.audio_storage = au.audio_storage, _FakeStorage()
    try:
        handler = _handlers(sessions, sent)["recording_batch"]
        response = asyncio.run(handler(_Request(), files=files, meta_json=json.dumps(meta)))
    finally:
        au.audio_storage = original
    with sessions() as db:
        rows = db.query(AudioFile).order_by(AudioFile.canonical_filename).all()
        stored = [(row.kind, row.role, row.chapter, row.canonical_filename) for row in rows]
    return response.status_code, json.loads(response.body), stored, sent


def _meta(role, chapter=""):
    return {"book_code": "КП", "chapter": chapter, "role": role, "actor_name": "Роман Сомов"}


class TestARoleThatIsNotHis:
    def test_it_arrives_as_an_audition_though_nobody_said_so(self):
        status, body, stored, _ = _post([_upload("Сатухух.wav")], [_meta("Сатухух")])

        assert status == 200 and body["ok"] is True
        assert stored == [("audition", "Сатухух", "", "KP_Satuhuh_RomanSomov_proba.wav")]

    def test_a_chapter_is_not_demanded_of_it(self):
        """Именно требование главы и загоняло слово «пробы» внутрь имени файла."""
        status, _, _, _ = _post([_upload("x.wav")], [_meta("Сатухух")])

        assert status == 200

    def test_two_attempts_at_one_role_are_both_kept(self):
        status, _, stored, _ = _post(
            [_upload("первая.wav"), _upload("вторая.wav")],
            [_meta("Сатухух"), _meta("Сатухух")],
        )

        assert status == 200
        assert [row[3] for row in stored] == [
            "KP_Satuhuh_RomanSomov_proba.wav",
            "KP_Satuhuh_RomanSomov_proba_2.wav",
        ]

    def test_the_owner_is_told_it_is_an_audition(self):
        _, _, _, sent = _post([_upload("проба.wav")], [_meta("Сатухух")])

        assert sent and "Пробы на роль" in sent[0]


class TestTheRoleHeHolds:
    def test_it_arrives_as_a_take(self):
        status, _, stored, _ = _post(
            [_upload("t.wav")], [_meta("Сатухух", "Глава4")], approved_role="Сатухух",
        )

        assert status == 200
        assert stored == [("take", "Сатухух", "Глава4", "KP_Ch04_Satuhuh_RomanSomov.wav")]

    def test_and_still_needs_its_chapter(self):
        status, body, stored, _ = _post([_upload("t.wav")], [_meta("Сатухух")], approved_role="Сатухух")

        assert status == 400
        assert any(item["error"] == "chapter_required" for item in body["items"])
        assert stored == []

    def test_the_same_take_twice_is_still_a_mistake(self):
        status, body, _, _ = _post(
            [_upload("a.wav"), _upload("b.wav")],
            [_meta("Сатухух", "Глава4"), _meta("Сатухух", "Глава4")],
            approved_role="Сатухух",
        )

        assert status == 400
        assert any(item["error"] == "duplicate_in_batch" for item in body["items"])


class TestWhoseNameGoesOnTheFile:
    """Имя актёра приходит от браузера — и потому его нельзя брать у кого угодно.

    Владельцу и автору называть чужое имя нужно: у них на руках оказываются записи,
    присланные мимо системы, и загрузить их надо за того, кто читал. Агенту — тем более:
    она ведёт актёров и грузит за них по роду занятий, а часть её актёров в системе
    есть, часть нет и не будет. Диктору — незачем, а возможность подписать запись чужим
    именем портит и покрытие, и уведомления, и расчёт по смете, причём тихо.

    Поэтому диктору имя подставляется из его сессии, что бы ни прислал браузер.
    """

    def _post(self, roles, meta_actor):
        sessions = _sessions("Сатухух")
        sent: list = []
        original, au.audio_storage = au.audio_storage, _FakeStorage()
        try:
            deps_handlers = _handlers(sessions, sent)
            handler = build_recording_handlers({
                **{
                    "is_authenticated": lambda request: True,
                    "has_any_role": lambda request, wanted: bool(set(roles) & set(wanted)),
                    "has_workspace_full_access": lambda request: True,
                    "session_payload": lambda request: {"display_name": "Роман Сомов", "sub": "tg_1"},
                    "is_agent": lambda request: "agent" in roles,
                    "SessionLocal": sessions,
                    "AudioFile": AudioFile,
                    "build_canonical_audio_filename": au.build_canonical_audio_filename,
                    "store_audio_file": lambda db, **kwargs: au.store_audio_file(db, safe_name=lambda s: s, **kwargs),
                    "send_telegram_message": lambda db, text: sent.append(text),
                    "recording_identity": lambda request, name: ("u1", name),
                    "mark_role_progress_recorded": lambda *args: 0,
                    "recording_workspace_payload": lambda db, **kwargs: {},
                    "require_recording_access": lambda request: None,
                    "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": {
                        "filename_chapter_index": 0,
                    },
                },
            })["recording_batch"]
            meta = [{"book_code": "КП", "chapter": "Глава4", "role": "Сатухух", "actor_name": meta_actor}]
            asyncio.run(handler(_Request(), files=[_upload("a.wav")], meta_json=json.dumps(meta)))
        finally:
            au.audio_storage = original
        with sessions() as db:
            return [row.actor_name for row in db.query(AudioFile).all()]

    def test_a_dictor_cannot_sign_a_file_with_another_name(self):
        assert self._post(["dictor"], "Сергей Зотов") == ["Роман Сомов"]

    def test_the_owner_can_upload_for_somebody_else(self):
        assert self._post(["admin"], "Сергей Зотов") == ["Сергей Зотов"]

    def test_so_can_the_author(self):
        assert self._post(["author"], "Сергей Зотов") == ["Сергей Зотов"]

    def test_so_can_the_agent(self):
        """Агент ведёт актёров и грузит за них по роду занятий — часть из них в
        системе есть, часть нет и не будет; ей нужно называть чужое имя каждый раз."""
        assert self._post(["agent"], "Сергей Зотов") == ["Сергей Зотов"]

    def test_leaving_it_empty_still_means_yourself(self):
        assert self._post(["admin"], "") == ["Роман Сомов"]
