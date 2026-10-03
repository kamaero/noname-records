"""Глава из имени файла против главы, выбранной в форме.

Тихонов Дмитрий прислал `KP_Ch34_Rgikt_TikhonovDmitriy.wav`, выбрав в форме главу 1:
файл лёг в главу 1, где его роль не говорит ни слова, и 82 МБ распознавания ушли впустую.

Оснастка ручек — та же, что в `tests/test_recording_duplicates.py`: настоящие
`store_audio_file`/`build_canonical_audio_filename`/`parse_batch_audio_filename` через
настоящие `recording_batch` и `dictor_pro_batch_validate`, хранилище подменено фейком.
"""
import asyncio
import io
import json
import logging

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.audio_uploads as au
from app.api.dictor_uploads import build_dictor_upload_handlers
from app.api.recording import build_recording_handlers
from app.db import Base
from app.models import AudioFile, Character, ScriptBook
from app.services.audio_uploads import apply_batch_overrides, parse_batch_audio_filename
from app.services.shared_runtime import normalize_role_label
from app.time_utils import utcnow_naive


def _parsed(name: str):
    return parse_batch_audio_filename(name, default_book_code="KP", default_actor_name="Тихонов Дмитрий",
                                      normalize_role_label=lambda s: s)


def _override(name: str, chapter: str, **extra):
    return apply_batch_overrides(_parsed(name), {"book_code": "KP", "chapter": chapter, "role": "Ргикьт",
                                                 "actor_name": "Тихонов Дмитрий", "kind": "take", **extra},
                                 normalize_role_label=lambda s: s)


def test_filename_chapter_index_is_kept():
    assert _parsed("KP_Ch34_Rgikt_TikhonovDmitriy.wav")["filename_chapter_index"] == 34
    assert _parsed("Rgikt_TikhonovDmitriy.wav")["filename_chapter_index"] == 0


def test_mismatch_refuses_the_file():
    item = _override("KP_Ch34_Rgikt_TikhonovDmitriy.wav", "Глава 1. Ничего особенного")
    assert "chapter_mismatch" in item["errors"]
    assert item["ok"] is False


def test_the_same_chapter_is_fine():
    item = _override("KP_Ch34_Rgikt_TikhonovDmitriy.wav", "Глава 34. Поздравляю с бракосочетанием")
    assert item["errors"] == []


def test_a_name_without_a_chapter_is_fine():
    item = _override("Rgikt_TikhonovDmitriy.wav", "Глава 1. Ничего особенного")
    assert "chapter_mismatch" not in item["errors"]


#: Живые имена, в которых главы нет, а число есть. Снисходительный разбор (`chapter_index`,
#: догадка о каноническом имени) читает их как главы 2, 3, 3 и 9 — и на отказ это число не
#: годится: диктору назвали бы главу, которой он не писал. `03-Za Maor.wav` актёр прислал
#: на самом деле (см. комментарий во фронтовом `BatchUpload.tsx`).
NAMES_THAT_ONLY_LOOK_LIKE_A_CHAPTER = (
    "Rgikt_2.wav",
    "Gamuk-3.wav",
    "03-Za Maor.wav",
    "KP_Rgikt_Tikhonov_2026-09-16.wav",
)


@pytest.mark.parametrize("name", NAMES_THAT_ONLY_LOOK_LIKE_A_CHAPTER)
def test_a_number_that_is_not_a_chapter_is_not_read_as_a_chapter(name):
    assert _parsed(name)["filename_chapter_index"] == 0


@pytest.mark.parametrize("name", NAMES_THAT_ONLY_LOOK_LIKE_A_CHAPTER)
def test_a_number_that_is_not_a_chapter_never_refuses_the_file(name):
    assert "chapter_mismatch" not in _override(name, "Глава 1. Ничего особенного")["errors"]


def test_the_naming_guess_stays_as_loose_as_it_was():
    """`chapter_index` кормит собой только догадку об имени — его нарочно не трогали."""
    assert _parsed("Rgikt_2.wav")["chapter_index"] == 2
    assert _parsed("KP_Rgikt_Tikhonov_2026-09-16.wav")["chapter_index"] == 9


def test_a_chapter_named_outright_is_read_and_refused():
    assert _parsed("KP_Ch34_Rgikt_TikhonovDmitriy.wav")["filename_chapter_index"] == 34
    item = _override("KP_Ch34_Rgikt_TikhonovDmitriy.wav", "Глава 1. Ничего особенного")
    assert "chapter_mismatch" in item["errors"]


def test_a_fix_token_is_not_taken_for_a_chapter():
    """`fix2` — номер пересъёмки; главу называет `Ch11`, и читается именно она."""
    assert _parsed("KP_Ch11_Gamuk_Zotov_fix2.wav")["filename_chapter_index"] == 11


def test_a_chapter_written_in_russian_is_read():
    assert _parsed("КП Глава 34 Ргикьт Тихонов.wav")["filename_chapter_index"] == 34


def test_the_spelling_the_studio_actually_uses_is_read():
    """`CH№32` — самое частое написание на проде: 23 файла из 152, почти все такие.
    Не знай разбор про `№`, ходовая раскладка имени проходила бы мимо сверки молча."""
    assert _parsed("KP_CH№32_Zar_Klopov_Zotov.wav")["filename_chapter_index"] == 32
    item = _override("KP_CH№32_Zar_Klopov_Zotov.wav", "Глава 1. Ничего особенного")
    assert "chapter_mismatch" in item["errors"]


def test_the_other_studio_spelling_without_an_h_is_read():
    """`C№19` без «h» — вторая живая раскладка на проде (8 файлов из 152)."""
    assert _parsed("KP_C№19_Pohotliviy_Jrez_Zotov.wav")["filename_chapter_index"] == 19


def test_a_lone_c_without_a_number_sign_is_not_a_chapter():
    """Буква «c» слишком частая, чтобы пускать её в отказ саму по себе."""
    assert _parsed("KP_C_19_Zotov.wav")["filename_chapter_index"] == 0


def test_a_long_number_is_not_chopped_into_a_chapter():
    """Дата не должна стать главой 202: три цифры от длинного числа не откусываются."""
    assert _parsed("KP_Ch_2026_Zotov.wav")["filename_chapter_index"] == 0


def test_a_name_written_through_dots_is_read():
    assert _parsed("KP.Ch34.wav")["filename_chapter_index"] == 34


def test_confirmation_removes_the_refusal():
    item = _override("KP_Ch34_Rgikt_TikhonovDmitriy.wav", "Глава 1. Ничего особенного", confirm_chapter=True)
    assert item["errors"] == []


def test_an_audition_is_never_refused_for_a_chapter():
    item = _override("KP_Ch34_Rgikt_TikhonovDmitriy_proba.wav", "", kind="audition")
    assert "chapter_mismatch" not in item["errors"]


class _FakeStorage:
    """Хранилище знает и куда положило, и что именно — тот же фейк, что в
    `test_recording_duplicates.py`."""

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
        return 10**12


class _Request:
    headers: dict = {}


class _JsonRequest:
    headers: dict = {}

    def __init__(self, payload: dict):
        self._payload = payload

    async def json(self):
        return self._payload


def _sessions():
    """Тихонов Дмитрий утверждён на роль «Ргикьт» в книге «КП»."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        book = ScriptBook(
            title="Крылья полумрака", source_filename="k.txt",
            source_format="txt", created_at=utcnow_naive(),
        )
        db.add(book)
        db.flush()
        db.add(Character(book_id=book.id, name="Ргикьт", actor_name="Тихонов Дмитрий"))
        db.commit()
    return sessions


def _upload(name: str) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(b"RIFF0000"))


def _recording_handlers(sessions, sent: list | None = None):
    return build_recording_handlers({
        "is_authenticated": lambda request: True,
        "has_any_role": lambda request, roles: True,
        "has_workspace_full_access": lambda request: True,
        "session_payload": lambda request: {"display_name": "Тихонов Дмитрий", "sub": "tg_1"},
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


def _post(sessions, files, meta, sent: list | None = None):
    original, au.audio_storage = au.audio_storage, _FakeStorage()
    try:
        handler = _recording_handlers(sessions, sent)["recording_batch"]
        response = asyncio.run(handler(_Request(), files=files, meta_json=json.dumps(meta)))
    finally:
        au.audio_storage = original
    body = json.loads(response.body)
    with sessions() as db:
        rows = db.query(AudioFile).all()
    return response.status_code, body, rows


def _meta(chapter: str, **extra) -> dict:
    return {"book_code": "KP", "chapter": chapter, "role": "Ргикьт", "actor_name": "Тихонов Дмитрий", **extra}


class TestTheUploadEndpointRefusesAMismatch:
    def test_the_wrong_chapter_is_refused_and_nothing_is_created(self):
        sessions = _sessions()
        status_code, body, rows = _post(
            sessions,
            [_upload("KP_Ch34_Rgikt_TikhonovDmitriy.wav")],
            [_meta("Глава 1. Ничего особенного")],
        )
        assert status_code == 400
        assert body["items"][0]["error"] == "chapter_mismatch"
        assert "глава 34" in body["message"]
        assert "глава 1" in body["message"]
        assert rows == []

    def test_confirm_chapter_lets_it_through(self):
        sessions = _sessions()
        status_code, body, rows = _post(
            sessions,
            [_upload("KP_Ch34_Rgikt_TikhonovDmitriy.wav")],
            [_meta("Глава 1. Ничего особенного", confirm_chapter=True)],
        )
        assert status_code == 200
        assert body["ok"] is True
        assert len(rows) == 1

    def test_the_matching_chapter_is_accepted(self):
        sessions = _sessions()
        status_code, body, rows = _post(
            sessions,
            [_upload("KP_Ch34_Rgikt_TikhonovDmitriy.wav")],
            [_meta("Глава 34. Поздравляю с бракосочетанием")],
        )
        assert status_code == 200
        assert body["ok"] is True
        assert len(rows) == 1


class TestAConfirmedOverrideLeavesATrace:
    """Подтверждённое расхождение не должно выглядеть как обычная загрузка.

    Ровно такой файл стоил 82 МБ платного распознавания и разбора вручную, и если
    подтверждение нигде не оставляет следа, разбирать второй такой случай будет не по чему.
    """

    def test_the_log_names_the_file_and_both_chapters(self, caplog):
        sessions = _sessions()
        sent: list = []
        with caplog.at_level(logging.WARNING, logger="app.api.recording"):
            status_code, _, rows = _post(
                sessions,
                [_upload("KP_Ch34_Rgikt_TikhonovDmitriy.wav")],
                [_meta("Глава 1. Ничего особенного", confirm_chapter=True)],
                sent,
            )
        assert status_code == 200 and len(rows) == 1
        messages = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
        assert any(
            "KP_Ch34_Rgikt_TikhonovDmitriy.wav" in message
            and "глава 34" in message
            and "глава 1" in message
            for message in messages
        ), messages

    def test_the_owner_notice_says_the_chapter_was_confirmed_by_hand(self):
        sessions = _sessions()
        sent: list = []
        _post(
            sessions,
            [_upload("KP_Ch34_Rgikt_TikhonovDmitriy.wav")],
            [_meta("Глава 1. Ничего особенного", confirm_chapter=True)],
            sent,
        )
        assert any(
            "⚠️ Глава подтверждена вручную: в имени файла глава 34, выбрана глава 1" in text
            for text in sent
        ), sent

    def test_a_notice_about_an_ordinary_batch_is_left_alone(self):
        sessions = _sessions()
        sent: list = []
        _post(
            sessions,
            [_upload("KP_Ch34_Rgikt_TikhonovDmitriy.wav")],
            [_meta("Глава 34. Поздравляю с бракосочетанием")],
            sent,
        )
        assert sent
        assert not any("подтверждена вручную" in text for text in sent)

    def test_a_batch_that_saved_nothing_leaves_no_line_about_a_confirmation(self, caplog):
        """Пачка отказала из-за ДРУГОГО файла — подтверждённый так и не лёг на диск.

        След пишется о записях, а не о намерениях: строка о файле, которого на сервере
        нет, отправила бы разбирающего искать несуществующее.
        """
        sessions = _sessions()
        sent: list = []
        with caplog.at_level(logging.WARNING, logger="app.api.recording"):
            status_code, body, rows = _post(
                sessions,
                [_upload("KP_Ch34_Rgikt_TikhonovDmitriy.wav"), _upload("KP_Ch34_NoRole.wav")],
                [
                    _meta("Глава 1. Ничего особенного", confirm_chapter=True),
                    {"book_code": "KP", "chapter": "Глава 1. Ничего особенного", "role": "",
                     "actor_name": "Тихонов Дмитрий"},
                ],
                sent,
            )
        assert status_code == 400
        assert [item["error"] for item in body["items"]] == ["role_required"]
        assert rows == []
        assert sent == []
        assert not [
            record for record in caplog.records
            if "подтверждена вручную" in record.getMessage()
        ], [record.getMessage() for record in caplog.records]

    def test_nothing_is_logged_when_there_is_nothing_to_confirm(self, caplog):
        sessions = _sessions()
        with caplog.at_level(logging.WARNING, logger="app.api.recording"):
            _post(
                sessions,
                [_upload("KP_Ch34_Rgikt_TikhonovDmitriy.wav")],
                [_meta("Глава 34. Поздравляю с бракосочетанием")],
            )
        assert not [
            record for record in caplog.records
            if record.levelno >= logging.WARNING and "подтверждена вручную" in record.getMessage()
        ]


class TestThePreviewFlagsTheSameMismatch:
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

    def test_the_preview_refuses_the_same_mismatch(self):
        sessions = _sessions()
        handler = self._handlers(sessions)["dictor_pro_batch_validate"]
        payload = {
            "files": [{"name": "KP_Ch34_Rgikt_TikhonovDmitriy.wav", "size": 4}],
            "overrides": [{
                "chapter": "Глава 1. Ничего особенного", "role": "Ргикьт",
                "actor_name": "Тихонов Дмитрий", "book_code": "KP",
            }],
            "book_code": "KP",
            "actor_name": "Тихонов Дмитрий",
        }

        response = asyncio.run(handler(_JsonRequest(payload)))
        body = json.loads(response.body)

        assert body["items"][0]["ok"] is False
        assert "chapter_mismatch" in body["items"][0]["errors"]
