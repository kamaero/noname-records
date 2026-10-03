"""«Уже загружен» — по содержимому, а не по имени.

Раньше повтор ловило совпадение итогового имени — и оно же било по дозаписи: полный
пересъём роли и короткий фикс реплики приходят каждый под своим обычным, предсказуемым
именем, а не случайным, так что имя сплошь и рядом совпадает с уже принятым файлом.
Решает содержимое (`AudioFile.md5`) в пределах главы и роли, любой актёр — актёр мог
прислать не тот файл роли по ошибке, и это не «уже загружен» (решение владельца
2026-09-15, `.superpowers/sdd/2026-09-15-rerecord-fixes/`).

Гоняет настоящие `store_audio_file`/`build_canonical_audio_filename` из
`app.services.audio_uploads` через настоящий `recording_batch`, как
`tests/test_audition_batch_endpoint.py` — хранилище подменено тем же `_FakeStorage`,
что считает md5 по-настоящему.
"""
import asyncio
import hashlib
import io
import json

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.audio_uploads as au
from app.api.dictor_uploads import build_dictor_upload_handlers
from app.api.recording import build_recording_handlers
from app.db import Base
from app.models import AudioFile, Character, ScriptBook
from app.services.shared_runtime import normalize_role_label
from app.time_utils import utcnow_naive


class _FakeStorage:
    """Хранилище знает и куда положило, и что именно: сверка «уже загружен» спрашивает
    сумму. Тот же фейк, что и в `test_audition_batch_endpoint.py`."""

    def write_file(self, key, payload, location="nas"):
        return hashlib.md5(payload).hexdigest()

    def write_stream(self, key, source, chunk_size=1024 * 1024, location="nas"):
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
        # Сторож места спрашивает диск при каждом приёме — этому фейку интересна
        # только сама запись, поэтому места всегда с запасом.
        return 10**12


class _Request:
    headers: dict = {}


class _JsonRequest:
    headers: dict = {}

    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


def _sessions(*approved: tuple[str, str]):
    """`approved` — пары (роль, актёр), утверждённые в касте книги «Крылья полумрака»."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    if approved:
        with sessions() as db:
            book = ScriptBook(
                title="Крылья полумрака", source_filename="k.txt",
                source_format="txt", created_at=utcnow_naive(),
            )
            db.add(book)
            db.flush()
            for role, actor in approved:
                db.add(Character(book_id=book.id, name=role, actor_name=actor))
            db.commit()
    return sessions


def _upload(name: str, content: bytes = b"RIFF0000") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def _recording_handlers(sessions, sent: list | None = None):
    return build_recording_handlers({
        "is_authenticated": lambda request: True,
        "has_any_role": lambda request, roles: True,
        "has_workspace_full_access": lambda request: True,
        "session_payload": lambda request: {"display_name": "Актёр", "sub": "tg_1"},
        "is_agent": lambda request: False,
        "SessionLocal": sessions,
        "AudioFile": AudioFile,
        "build_canonical_audio_filename": au.build_canonical_audio_filename,
        "store_audio_file": lambda db, **kwargs: au.store_audio_file(db, safe_name=lambda s: s, **kwargs),
        "send_telegram_message": lambda db, text: (sent.append(text) if sent is not None else None),
        "recording_identity": lambda request, name: ("u1", name),
        "mark_role_progress_recorded": lambda *args: 0,
        "recording_workspace_payload": lambda db, **kwargs: {},
        "require_recording_access": lambda request: None,
        "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": au.parse_batch_audio_filename(
            filename,
            default_book_code=default_book_code,
            default_actor_name=default_actor_name,
            normalize_role_label=normalize_role_label,
        ),
    })


def _post(sessions, files, meta):
    """`stored_by_id` держит ВСЕ строки на сейчас (`AudioFile.id` — uuid, по нему не
    сортируются) — порядок этого вызова берётся из `body["saved"]`, он уже в порядке
    приёма и несёт `canonical_filename`; `stored_by_id` нужен только за тем, чего в
    ответе нет — `stored_key`."""
    original, au.audio_storage = au.audio_storage, _FakeStorage()
    try:
        handler = _recording_handlers(sessions)["recording_batch"]
        response = asyncio.run(handler(_Request(), files=files, meta_json=json.dumps(meta)))
    finally:
        au.audio_storage = original
    body = json.loads(response.body)
    with sessions() as db:
        stored_by_id = {
            row.id: (row.role, row.chapter, row.canonical_filename, row.stored_key, row.md5)
            for row in db.query(AudioFile).all()
        }
    return response.status_code, body, stored_by_id


def _meta(role: str, chapter: str, actor: str = "Актёр") -> dict:
    return {"book_code": "КП", "chapter": chapter, "role": role, "actor_name": actor}


class TestARerecordedRoleUnderTheSameName:
    """Дозапись всей роли приходит под тем же обычным именем — и это больше не повод
    считать её повтором уже принятого файла: решает содержимое."""

    def test_different_content_under_the_same_filename_is_kept_as_a_second_file(self):
        sessions = _sessions(("Гамук", "Актёр"))
        status_a, body_a, stored_a = _post(
            sessions, [_upload("Гамук.wav", b"AAAA")], [_meta("Гамук", "Глава11")],
        )
        assert status_a == 200 and body_a["ok"] is True
        assert len(stored_a) == 1

        status_b, body_b, stored_b = _post(
            sessions, [_upload("Гамук.wav", b"BBBB")], [_meta("Гамук", "Глава11")],
        )

        assert status_b == 200 and body_b["ok"] is True
        assert len(stored_b) == 2
        assert body_b["saved"][0]["canonical_filename"].endswith("_2.wav")
        first_id, second_id = next(iter(stored_a)), body_b["saved"][0]["id"]
        assert stored_b[first_id][3] != stored_b[second_id][3]  # разные stored_key


class TestAShortFix:
    def test_a_file_named_with_a_fix_token_gets_its_own_fix_name(self):
        sessions = _sessions(("Гамук", "Актёр"))
        _post(sessions, [_upload("Гамук.wav", b"AAAA")], [_meta("Гамук", "Глава11")])

        status, body, stored = _post(
            sessions, [_upload("KP_Ch11_Gamuk_fix1.wav", b"CCCC")], [_meta("Гамук", "Глава11")],
        )

        assert status == 200 and body["ok"] is True
        assert len(stored) == 2
        assert body["saved"][0]["canonical_filename"].endswith("_fix1.wav")


class TestTheExactSameContentAgain:
    def test_it_is_refused_as_already_uploaded_no_matter_the_name(self):
        sessions = _sessions(("Гамук", "Актёр"))
        _post(sessions, [_upload("Гамук.wav", b"AAAA")], [_meta("Гамук", "Глава11")])

        status, body, stored = _post(
            sessions, [_upload("совсем другое имя.wav", b"AAAA")], [_meta("Гамук", "Глава11")],
        )

        assert status == 400
        assert any(item["error"] == "duplicate_in_storage" for item in body["items"])
        assert len(stored) == 1  # второй файл не лёг

    def test_the_refusal_names_the_file_in_words(self):
        """Экран показывает `message`, а не `validation_failed`."""
        sessions = _sessions(("Гамук", "Актёр"))
        _post(sessions, [_upload("KP_Ch11_Gamuk.wav", b"AAAA")], [_meta("Гамук", "Глава11")])

        status, body, _ = _post(
            sessions, [_upload("KP_Ch11_Gamuk_again.wav", b"AAAA")], [_meta("Гамук", "Глава11")],
        )

        assert status == 400
        assert body["message"] == "KP_Ch11_Gamuk_again.wav: этот же файл уже загружен в эту роль и главу"


class TestTwoDifferentFilesOfOneRoleInOneBatch:
    def test_both_are_saved_under_different_storage_keys(self):
        sessions = _sessions(("Гамук", "Актёр"))

        status, body, stored = _post(
            sessions,
            [_upload("а.wav", b"AAAA"), _upload("б.wav", b"BBBB")],
            [_meta("Гамук", "Глава11"), _meta("Гамук", "Глава11")],
        )

        assert status == 200 and body["ok"] is True
        assert len(stored) == 2
        key_a, key_b = (stored[row["id"]][3] for row in body["saved"])
        assert key_a != key_b


class TestTwoIdenticalFilesInOneBatch:
    def test_it_is_a_duplicate_in_batch_even_under_different_names(self):
        sessions = _sessions(("Гамук", "Актёр"))

        status, body, stored = _post(
            sessions,
            [_upload("копия1.wav", b"AAAA"), _upload("копия2.wav", b"AAAA")],
            [_meta("Гамук", "Глава11"), _meta("Гамук", "Глава11")],
        )

        assert status == 400
        errors = {item["error"] for item in body["items"]}
        assert "duplicate_in_batch" in errors
        assert stored == {}
        assert body["message"] == "копия1.wav: в пачке два одинаковых файла; копия2.wav: в пачке два одинаковых файла"


class TestTheSameRecordingSentForAnotherRole:
    """Актёр мог прислать не тот файл роли по ошибке — решает система по роли и
    главе, а не по тому, что это содержимое где-то уже встречалось: не дубль."""

    def test_it_is_accepted_not_flagged_as_a_duplicate(self):
        sessions = _sessions(("Гамук", "Актёр"), ("Сатухух", "Актёр"))
        _post(sessions, [_upload("Гамук.wav", b"SAME")], [_meta("Гамук", "Глава11")])

        status, body, stored = _post(
            sessions, [_upload("Сатухух.wav", b"SAME")], [_meta("Сатухух", "Глава11")],
        )

        assert status == 200 and body["ok"] is True
        assert len(stored) == 2


class TestThePreviewNoLongerChecksContent:
    """`dictor_pro_batch_validate` видит только имя, не содержимое: об «уже
    загружен» по имени больше не может судить, эту работу теперь делает сама
    загрузка."""

    def _handlers(self, sessions):
        return build_dictor_upload_handlers({
            "is_authenticated": lambda request: True,
            "has_any_role": lambda request, roles: True,
            "has_workspace_full_access": lambda request: True,
            "SessionLocal": sessions,
            "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": au.parse_batch_audio_filename(
                filename,
                default_book_code=default_book_code,
                default_actor_name=default_actor_name,
                normalize_role_label=normalize_role_label,
            ),
            "apply_batch_overrides": lambda parsed, override=None: au.apply_batch_overrides(
                parsed, override, normalize_role_label=normalize_role_label,
            ),
        })

    def test_a_filename_that_already_exists_in_storage_still_previews_ok(self):
        sessions = _sessions(("Гамук", "Актёр"))
        _post(sessions, [_upload("Гамук.wav", b"AAAA")], [_meta("Гамук", "Глава11")])

        handler = self._handlers(sessions)["dictor_pro_batch_validate"]
        payload = {
            "files": [{"name": "Гамук.wav", "size": 4}],
            "overrides": [{"chapter": "Глава11", "role": "Гамук", "actor_name": "Актёр", "book_code": "КП"}],
            "book_code": "КП",
            "actor_name": "Актёр",
        }

        response = asyncio.run(handler(_JsonRequest(payload)))
        body = json.loads(response.body)

        assert body["items"][0]["ok"] is True
        assert "duplicate_in_storage" not in body["items"][0]["errors"]
        assert "duplicate_in_batch" not in body["items"][0]["errors"]
