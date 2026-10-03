"""Файл проекта Audition рядом с папкой главы на NAS — событие «глава дописана».

Отдельного «переноса главы» нет: дубли к этому моменту давно зеркалированы фоновым
циклом (`audio_mirror.mirror_once`). Событие добавляет только `.sesx` и отметку на
главе. Условий готовности три сразу, потому что каждое ловит своё: роли отвечают за
полноту записи, распознавание — за то, что прочитано всё, а сверка — за то, что файлы
целы.
"""
import asyncio
import io
import json
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.recording import build_recording_handlers
from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter, SoundMarker
from app.services import audio_storage, chapter_delivery, nas_health
from app.services.audio_uploads import TAKE
from app.services.chapter_delivery import _chapter_label, archive_chapter_session
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment

_CHAPTER_ID = ""


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "rec"))
    monkeypatch.setattr(audio_storage.settings, "audio_nas_path", str(tmp_path / "nas"))
    # По умолчанию зеркало «живое»: тесты этого файла проверяют логику архивации,
    # а не сторожа NAS — тому отдельный тест ниже с `record_probe(False)`.
    nas_health.record_probe(True)
    return tmp_path


class _Request:
    headers: dict = {}


def _upload(name: str) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(b"RIFF0000"))


def _sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _book_with_one_chapter(db, *, roles: dict[str, int]):
    """Книга с одной главой, где заданные роли говорят заданное число реплик.

    Роли собираются как настоящие v2-атрибуции (сегмент + span), а не подменой
    `chapter_role_counts`: `build_chapter_session` (который вызывает
    `archive_chapter_session`) читает сценарий по-настоящему, и без сегментов у
    главы не было бы ни одной роли на треке.

    `ScriptBook` требует `source_filename` и `source_format`, а `book_code` у него
    нет вовсе — это поле есть только у `AudioFile`.
    """
    book = ScriptBook(title="Крылья полумрака", source_filename="k.txt", source_format="txt")
    db.add(book)
    db.flush()
    chapter = ScriptChapter(book_id=book.id, chapter_index=5, chapter_title="Глава 5. Проба пера")
    db.add(chapter)
    db.flush()

    ordinal = 0
    for role, count in roles.items():
        for i in range(int(count)):
            text = f"{role} говорит реплику номер {i}."
            segment_id = f"{chapter.id}:{ordinal:05d}"
            db.add(V2Segment(
                id=segment_id, book_id=book.id, chapter_id=chapter.id,
                ordinal=ordinal, kind="paragraph", text=text,
            ))
            db.flush()
            db.add(V2Attribution(
                id=str(uuid.uuid4()), segment_id=segment_id, version=1, speaker=role,
                span_start=0, span_end=len(text), source="llm", created_at=utcnow_naive(),
            ))
            ordinal += 1
    db.flush()
    return book.id, chapter.id


def _take(db, chapter_id, *, role, mirror_state=""):
    chapter = db.get(ScriptChapter, chapter_id)
    item = AudioFile(
        book_code="KP", original_filename=f"{role}.wav", stored_key=f"uploads/raw/2026/09/{role}.wav",
        canonical_filename=f"KP_Ch05_{role}.wav", mime_type="audio/wav", size_bytes=100,
        chapter=_chapter_label(chapter), role=role, kind=TAKE, duration_seconds=12.5,
        mirror_state=mirror_state,
    )
    db.add(item)
    db.flush()
    return item


def _finished(db, monkeypatch, *, missing_lines=0, broken=False, ready=True):
    """Глава со всеми тремя условиями под контролем теста.

    Условия подменяются, а не собираются: `chapter_is_ready` считает роли по
    v2-атрибуциям, а `book_recording_status` — по заданиям распознавания, и
    строить в тесте обе цепочки значило бы проверять чужой код вместо своего.
    """
    from app.services import audio_integrity
    monkeypatch.setattr(audio_integrity, "chapter_is_ready", lambda db, cid: ready)
    monkeypatch.setattr(audio_integrity, "chapter_has_broken_files", lambda db, cid: broken)
    monkeypatch.setattr(chapter_delivery, "book_recording_status", lambda db, bid: {
        "chapters": [{"chapter_id": _CHAPTER_ID, "asr_files": 1, "missing_lines": missing_lines}]
    })


def test_a_chapter_that_is_not_ready_gets_no_session_file(roots, monkeypatch):
    global _CHAPTER_ID
    sessions = _sessions()
    with sessions() as db:
        book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
        _CHAPTER_ID = chapter_id
        _finished(db, monkeypatch, ready=False)
        assert archive_chapter_session(db, chapter_id) == {"written": False, "reason": "not_ready", "replaced": ""}
        assert not list((roots / "nas").rglob("*.sesx"))


def test_a_chapter_with_gaps_in_recognition_gets_no_session_file(roots, monkeypatch):
    global _CHAPTER_ID
    sessions = _sessions()
    with sessions() as db:
        book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
        _CHAPTER_ID = chapter_id
        _finished(db, monkeypatch, missing_lines=3)
        assert archive_chapter_session(db, chapter_id) == {"written": False, "reason": "asr_gaps", "replaced": ""}
        assert not list((roots / "nas").rglob("*.sesx"))


def test_a_finished_chapter_gets_its_session_next_to_the_folder(roots, monkeypatch):
    global _CHAPTER_ID
    sessions = _sessions()
    with sessions() as db:
        book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
        _CHAPTER_ID = chapter_id
        _take(db, chapter_id, role="Дгарнин")
        db.commit()
        _finished(db, monkeypatch)
        result = archive_chapter_session(db, chapter_id)
        db.commit()
        assert result == {"written": True, "reason": "", "replaced": ""}
        written = list((roots / "nas").rglob("*.sesx"))
        assert len(written) == 1
        # BOM в начале — сессии Audition пишутся с ним по-настоящему (см.
        # `audition_session.build_session_xml`), а `str.lstrip()` без аргумента его
        # не снимает: тот же приём, что и в `tests/test_audition_session.py::_parse`.
        assert written[0].read_text(encoding="utf-8").lstrip("﻿").startswith("<?xml")
        chapter = db.get(ScriptChapter, chapter_id)
        assert chapter.session_archived_at is not None


class TestArchivingStoresTheSkippedMarkerCount:
    """Число маркеров без места — то, что `build_chapter_session_with_stats` вернула
    вместе с XML, а не то, что таблица ASR посчитала бы заново (Часть 4, задача 3)."""

    def test_a_written_session_stores_the_count(self, roots, monkeypatch):
        global _CHAPTER_ID
        sessions = _sessions()
        with sessions() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 2})
            _CHAPTER_ID = chapter_id
            _take(db, chapter_id, role="Дгарнин")
            # Без распознавания ни одна реплика не легла на таймлайн — маркеру
            # решительно негде встать, он гарантированно пропущен.
            db.add(SoundMarker(book_id=book_id, chapter_id=chapter_id, segment_id=f"{chapter_id}:00005",
                               kind="transition", payload={"what": "флешбэк"}, text_sha256="x"))
            db.commit()
            _finished(db, monkeypatch)

            result = archive_chapter_session(db, chapter_id)
            db.commit()

            assert result["written"] is True
            assert db.get(ScriptChapter, chapter_id).session_markers_skipped == 1

    def test_an_unchanged_rebuild_refreshes_the_count_too(self, roots, monkeypatch):
        global _CHAPTER_ID
        sessions = _sessions()
        with sessions() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 2})
            _CHAPTER_ID = chapter_id
            _take(db, chapter_id, role="Дгарнин")
            db.add(SoundMarker(book_id=book_id, chapter_id=chapter_id, segment_id=f"{chapter_id}:00005",
                               kind="transition", payload={"what": "флешбэк"}, text_sha256="x"))
            db.commit()
            _finished(db, monkeypatch)
            assert archive_chapter_session(db, chapter_id)["written"] is True
            db.commit()

            chapter = db.get(ScriptChapter, chapter_id)
            chapter.session_markers_skipped = 0  # словно посчитан не был
            db.commit()

            result = archive_chapter_session(db, chapter_id)
            db.commit()

            assert result == {"written": False, "reason": "unchanged", "replaced": ""}
            assert db.get(ScriptChapter, chapter_id).session_markers_skipped == 1


def test_session_fingerprint_ignores_component_guids(roots, monkeypatch):
    """`build_chapter_session` даёт разный XML на каждый вызов — GUID компонентов
    случаен и обязан оставаться случайным (см. `audition_session._component`: Audition
    не терпит повторов внутри файла) — но `chapter_delivery.session_fingerprint` должен
    видеть одну и ту же раскладку как один и тот же отпечаток. Иначе «изменилось ли»
    решала бы удача генератора UUID, а не сверка."""
    global _CHAPTER_ID
    sessions = _sessions()
    with sessions() as db:
        book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
        _CHAPTER_ID = chapter_id
        _take(db, chapter_id, role="Дгарнин")
        db.commit()

        first = chapter_delivery.build_chapter_session(db, chapter_id, relative=True)
        second = chapter_delivery.build_chapter_session(db, chapter_id, relative=True)

        assert first != second
        assert chapter_delivery.session_fingerprint(first) == chapter_delivery.session_fingerprint(second)


class TestUploadNeverTouchesTheNas:
    """§4 спеки прямым текстом запрещает NFS в пути приёма: мёртвое `hard`-монтирование
    не отдаёт ошибку, а виснет навсегда, и загрузка не может ни повиснуть на NAS, ни
    провалиться из-за него. `try/except Exception` вокруг постановки очередей от этого
    не спасает — зависание не исключение, оно съедает поток и никогда не возвращается.

    Раньше `archive_chapter_session` звалась прямо из ручки загрузки — ровно тот путь,
    которым диктор мог дописать последнюю пачку главы и упереться в молчащий NAS.
    Теперь запись на зеркало живёт только в фоновом цикле (`app.workers.mirror.mirror_loop`),
    а ручка загрузки её не знает вовсе.
    """

    def _seed_take(self, sessions) -> str:
        """Книга и опубликованная глава — чтобы `find_chapter_for_take` нашёл главу
        по коду книги и заголовку из формы, как в `tests/test_recording_disk_full.py`."""
        with sessions() as db:
            book = ScriptBook(
                title="Крылья полумрака", source_filename="k.txt",
                source_format="txt", created_at=utcnow_naive(),
            )
            db.add(book)
            db.flush()
            chapter = ScriptChapter(book_id=book.id, chapter_index=1, chapter_title="Глава1", status="published")
            db.add(chapter)
            db.add(Character(book_id=book.id, name="Дгарнин", actor_name="Актёр"))
            db.commit()
            return str(chapter.id)

    def _store(self, db, *, original_filename, book_code, chapter, role, actor_name, kind, **_kwargs):
        return SimpleNamespace(
            id=f"id-{original_filename}", original_filename=original_filename,
            canonical_filename=original_filename, chapter=chapter, role=role,
            actor_name=actor_name, kind=kind,
        )

    def _handlers(self, sessions):
        return build_recording_handlers({
            "is_authenticated": lambda request: True,
            "has_any_role": lambda request, roles: True,
            "has_workspace_full_access": lambda request: True,
            "session_payload": lambda request: {"display_name": "Актёр", "sub": "tg_1"},
            "is_agent": lambda request: False,
            "SessionLocal": sessions,
            "AudioFile": AudioFile,
            "build_canonical_audio_filename": lambda book_code, chapter, role, actor_name, original_filename, **kw: (
                original_filename or "audio.wav"
            ),
            "store_audio_file": self._store,
            "send_telegram_message": lambda db, text: None,
            "recording_identity": lambda request, name: ("u1", name),
            "mark_role_progress_recorded": lambda *args: 0,
            "recording_workspace_payload": lambda db, **kwargs: {},
            "require_recording_access": lambda request: None,
            "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": {
                "filename_chapter_index": 0,
            },
        })

    def test_a_batch_that_finishes_the_chapter_never_calls_write_file_on_the_nas(self, roots, monkeypatch):
        global _CHAPTER_ID
        sessions = _sessions()
        chapter_id = self._seed_take(sessions)
        _CHAPTER_ID = chapter_id
        # Все три условия «глава дописана» — подменены тем же приёмом, что и в
        # `_finished`, но без своей копии: monkeypatch держится за модуль, а не за
        # конкретную сессию БД, так что подмена работает и внутри ручки загрузки.
        with sessions() as db:
            _finished(db, monkeypatch)

        calls: list[str] = []

        def _spy_write_file(key, data, *, location="local"):
            calls.append(location)
            return "deadbeef"

        monkeypatch.setattr(audio_storage, "write_file", _spy_write_file)
        monkeypatch.setattr("app.api.recording.enqueue_asr_for_chapter", lambda cid: None)
        handler = self._handlers(sessions)["recording_batch"]
        meta = [{"book_code": "КП", "chapter": "Глава1", "role": "Дгарнин", "actor_name": "Актёр"}]

        response = asyncio.run(handler(_Request(), files=[_upload("fits.wav")], meta_json=json.dumps(meta)))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["ok"] is True
        # Главная проверка раунда: ни разу не позвали запись с `location="nas"`,
        # чем бы ни закончилась постановка очередей внутри ручки.
        assert "nas" not in calls


class TestArchiveRefusesOnASilentMirror:
    """Настроенный, но не отвечающий NAS — не то же самое, что не настроенный вовсе.

    `resolve_path(..., location="nas")` на пустом корне бросает `RuntimeError` сразу;
    настроенный, но мёртвый (`hard`-монтирование) корень не бросает ничего — он висит.
    Разница важна: `no_nas` — «зеркала нет вовсе», это никогда само не пройдёт;
    молчащий NAS может ожить к следующему кругу `mirror_loop`, поэтому у него другая
    причина отказа.
    """

    def test_a_silent_nas_gives_a_refusal_not_an_attempt(self, roots, monkeypatch):
        global _CHAPTER_ID
        sessions = _sessions()
        with sessions() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
            _CHAPTER_ID = chapter_id
            _take(db, chapter_id, role="Дгарнин")
            db.commit()
            _finished(db, monkeypatch)
            nas_health.record_probe(False)  # NAS настроен, но сторож видит молчание

            result = archive_chapter_session(db, chapter_id)

            assert result == {"written": False, "reason": "nas_offline", "replaced": ""}
            assert not list((roots / "nas").rglob("*.sesx"))
            chapter = db.get(ScriptChapter, chapter_id)
            assert chapter.session_archived_at is None


class TestArchiveRefusesOnAMismatchedMirrorCopy:
    """`chapter_has_broken_files` смотрит только `verify_state` — целостность оригинала
    на сервере. `mirror_state` дубля не проверяет никто: у копии на NAS могла разойтись
    сумма, круг зеркалирования исключает такой файл из копирования навсегда (см.
    `audio_mirror.mirror_once`), а `.sesx` всё равно ложится на NAS и ссылается на
    файлы по именам. Владелец открывает сессию с NAS и получает битый клип, хотя
    экран сказал «проект ✓»."""

    def test_a_chapter_with_a_mismatched_mirror_copy_gets_no_session_file(self, roots, monkeypatch):
        global _CHAPTER_ID
        sessions = _sessions()
        with sessions() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
            _CHAPTER_ID = chapter_id
            from app.services.audio_mirror import MISMATCH as MIRROR_MISMATCH
            _take(db, chapter_id, role="Дгарнин", mirror_state=MIRROR_MISMATCH)
            db.commit()
            _finished(db, monkeypatch)

            result = archive_chapter_session(db, chapter_id)

            assert result == {"written": False, "reason": "mirror_broken", "replaced": ""}
            assert not list((roots / "nas").rglob("*.sesx"))
            chapter = db.get(ScriptChapter, chapter_id)
            assert chapter.session_archived_at is None

    def test_a_chapter_with_a_healthy_mirror_copy_still_gets_its_session(self, roots, monkeypatch):
        """Отрицательный контроль: обычный `mirror_state` (пустой или `ok`) не должен
        сам по себе запирать архив — иначе находка стала бы регрессией на ровном месте."""
        global _CHAPTER_ID
        sessions = _sessions()
        with sessions() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
            _CHAPTER_ID = chapter_id
            _take(db, chapter_id, role="Дгарнин", mirror_state="ok")
            db.commit()
            _finished(db, monkeypatch)

            result = archive_chapter_session(db, chapter_id)
            db.commit()

            assert result == {"written": True, "reason": "", "replaced": ""}


class TestArchiveRefusesWhenStorageRootsOverlap:
    """`audio_mirror.mirror_once` уже отказывается копировать при `roots_overlap()` —
    иначе обе копии легли бы на один диск. `archive_chapter_session` такой проверки
    не делала: при перекрытых корнях она положила бы `.sesx` на локальный диск,
    поставила отметку — и экран показал бы «проект ✓» про файл, которого на NAS нет."""

    def test_overlapping_roots_get_a_refusal_not_a_local_write(self, roots, monkeypatch):
        global _CHAPTER_ID
        sessions = _sessions()
        with sessions() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
            _CHAPTER_ID = chapter_id
            _take(db, chapter_id, role="Дгарнин")
            db.commit()
            _finished(db, monkeypatch)
            from app.services import audio_mirror
            monkeypatch.setattr(audio_mirror, "roots_overlap", lambda: True)

            result = archive_chapter_session(db, chapter_id)

            assert result == {"written": False, "reason": "roots_overlap", "replaced": ""}
            assert not list((roots / "nas").rglob("*.sesx"))
            chapter = db.get(ScriptChapter, chapter_id)
            assert chapter.session_archived_at is None


class TestBackgroundArchivingIsIdempotent:
    """`mirror_loop` — единственное законное место записи на NAS вне запроса: он уже
    крутится по расписанию, уже спрашивает сторожа и уже пишет зеркало. Файл проекта
    кладётся туда же, одним кругом позже, и только для глав, у которых `session_archived_at`
    пуст — второй круг по уже архивированной главе не должен трогать NAS повторно."""

    def test_a_finished_chapter_is_archived_once_and_left_alone_on_the_next_pass(self, roots, monkeypatch):
        global _CHAPTER_ID
        sessions = _sessions()
        with sessions() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
            _CHAPTER_ID = chapter_id
            _take(db, chapter_id, role="Дгарнин")
            db.commit()
            _finished(db, monkeypatch)

            first = chapter_delivery.archive_pending_chapter_sessions(db)
            db.commit()
            assert first == {"archived": 1, "skipped": 0, "rebuilt": 0}
            written = list((roots / "nas").rglob("*.sesx"))
            assert len(written) == 1
            archived_at = db.get(ScriptChapter, chapter_id).session_archived_at
            assert archived_at is not None

            second = chapter_delivery.archive_pending_chapter_sessions(db)
            db.commit()
            assert second == {"archived": 0, "skipped": 0, "rebuilt": 0}
            # Файл не тронут: имя то же, второй записи не было — а если бы была,
            # `session_archived_at` не остался бы тем же самым значением.
            assert list((roots / "nas").rglob("*.sesx")) == written
            assert db.get(ScriptChapter, chapter_id).session_archived_at == archived_at

    def test_a_chapter_with_no_takes_at_all_is_never_even_looked_at(self, roots, monkeypatch):
        """Ограничение по меткам глав с дублями — иначе фон пересчитывал бы статус
        (готовность, распознавание, сверку) для каждой главы книги на каждом круге."""
        sessions = _sessions()
        with sessions() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
            # Дублей нет — `chapter_is_ready`/`book_recording_status` не подменены и
            # звать их незачем: если бы фон всё равно их звал, тест бы это не поймал,
            # но упавший на реальном коде `chapter_is_ready` без подмены — поймал бы.
            db.commit()

            result = chapter_delivery.archive_pending_chapter_sessions(db)

            assert result == {"archived": 0, "skipped": 0, "rebuilt": 0}


def _second_chapter(db, book_id, *, index, role, count=3):
    """Ещё одна глава той же книги, с дублем и настоящими сегментами."""
    chapter = ScriptChapter(book_id=book_id, chapter_index=index,
                            chapter_title=f"Глава {index}. Вторая")
    db.add(chapter)
    db.flush()
    for i in range(count):
        text = f"{role} говорит реплику номер {i}."
        segment_id = f"{chapter.id}:{i:05d}"
        db.add(V2Segment(id=segment_id, book_id=book_id, chapter_id=chapter.id,
                         ordinal=i, kind="paragraph", text=text))
        db.flush()
        db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, version=1,
                             speaker=role, span_start=0, span_end=len(text),
                             source="llm", created_at=utcnow_naive()))
    db.add(AudioFile(
        book_code="KP", original_filename=f"{role}.wav",
        stored_key=f"KP/Chapter{index:02d}/{role}.wav",
        canonical_filename=f"KP_Ch{index:02d}_{role}.wav", mime_type="audio/wav",
        size_bytes=100, chapter=_chapter_label(chapter), role=role, kind=TAKE,
        duration_seconds=12.5,
    ))
    db.flush()
    return chapter.id


class TestTheBackgroundPassIsCheapAndSurvivesFailures:
    def test_the_book_status_is_computed_once_per_pass_not_once_per_chapter(self, roots, monkeypatch):
        """`book_recording_status` считает книгу ЦЕЛИКОМ: обход сегментов, атрибуций
        и всех дублей. Пока распознавания ждут несколько глав сразу — а так бывает
        всю запись, — вызов на каждую главу означал бы столько же полных обходов
        книги каждые несколько минут, вечно."""
        from app.services import audio_integrity

        sessions = _sessions()
        with sessions() as db:
            book_id, first_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
            _take(db, first_id, role="Дгарнин")
            second_id = _second_chapter(db, book_id, index=6, role="Сатухух")
            db.commit()

            monkeypatch.setattr(audio_integrity, "chapter_is_ready", lambda db, cid: True)
            monkeypatch.setattr(audio_integrity, "chapter_has_broken_files", lambda db, cid: False)
            calls: list[str] = []

            def _counted_status(db, book):
                calls.append(str(book))
                return {"chapters": [
                    {"chapter_id": first_id, "asr_files": 1, "missing_lines": 0},
                    {"chapter_id": second_id, "asr_files": 1, "missing_lines": 0},
                ]}

            monkeypatch.setattr(chapter_delivery, "book_recording_status", _counted_status)

            result = chapter_delivery.archive_pending_chapter_sessions(db)
            db.commit()

            assert result == {"archived": 2, "skipped": 0, "rebuilt": 0}
            assert calls == [book_id], "статус книги пересчитан больше одного раза за круг"

    def test_a_chapter_that_blows_up_does_not_roll_back_the_ones_already_written(self, roots, monkeypatch):
        """Коммит на каждую главу, а не один на круг. Иначе сбой на последней главе
        отправлял бы уже записанные `.sesx` на второй заход — файлы на NAS лежат,
        а отметок в базе нет, и круг переписывал бы их снова и снова."""
        from app.services import audio_integrity

        sessions = _sessions()
        with sessions() as db:
            book_id, first_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
            _take(db, first_id, role="Дгарнин")
            second_id = _second_chapter(db, book_id, index=6, role="Сатухух")
            db.commit()

            monkeypatch.setattr(audio_integrity, "chapter_is_ready", lambda db, cid: True)
            monkeypatch.setattr(audio_integrity, "chapter_has_broken_files", lambda db, cid: False)
            monkeypatch.setattr(chapter_delivery, "book_recording_status", lambda db, bid: {
                "chapters": [
                    {"chapter_id": first_id, "asr_files": 1, "missing_lines": 0},
                    {"chapter_id": second_id, "asr_files": 1, "missing_lines": 0},
                ]
            })
            real_build = chapter_delivery.build_chapter_session_with_stats

            def _explodes_on_the_second(db, chapter_id, **kwargs):
                if chapter_id == second_id:
                    raise RuntimeError("сборка сессии сорвалась")
                return real_build(db, chapter_id, **kwargs)

            monkeypatch.setattr(chapter_delivery, "build_chapter_session_with_stats", _explodes_on_the_second)

            with pytest.raises(RuntimeError):
                chapter_delivery.archive_pending_chapter_sessions(db)
            # Откат — то, что сделает вызывающий или закрытие сессии после исключения.
            # Без него тест ничего не доказывает: незакоммиченная отметка всё равно
            # видна в той же сессии, и беззубый тест зеленел бы и без коммита на главу.
            db.rollback()

            # Первая глава пережила исключение на второй: её отметка закоммичена.
            assert db.get(ScriptChapter, first_id).session_archived_at is not None
            assert db.get(ScriptChapter, second_id).session_archived_at is None

    def test_the_background_pass_writes_nothing_while_the_nas_is_silent(self, roots, monkeypatch):
        """Сторож спрашивается и на фоновом круге, а не только при прямом вызове:
        круг — единственное место, которое ходит на NAS само, без человека."""
        global _CHAPTER_ID
        sessions = _sessions()
        with sessions() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
            _CHAPTER_ID = chapter_id
            _take(db, chapter_id, role="Дгарнин")
            db.commit()
            _finished(db, monkeypatch)
            nas_health.record_probe(False)

            result = chapter_delivery.archive_pending_chapter_sessions(db)
            db.commit()

            assert result == {"archived": 0, "skipped": 1, "rebuilt": 0}
            assert list((roots / "nas").rglob("*.sesx")) == []
            assert db.get(ScriptChapter, chapter_id).session_archived_at is None


class TestAnOutdatedSessionIsRebuilt:
    def _archived(self, db, monkeypatch, roots):
        global _CHAPTER_ID
        book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 5})
        _CHAPTER_ID = chapter_id
        _take(db, chapter_id, role="Дгарнин")
        db.commit()
        _finished(db, monkeypatch)
        assert archive_chapter_session(db, chapter_id)["written"] is True
        db.commit()
        return chapter_id

    def test_marking_touches_only_archived_chapters(self, roots, monkeypatch):
        from app.services.chapter_delivery import mark_session_outdated
        with _sessions()() as db:
            book_id, chapter_id = _book_with_one_chapter(db, roles={"Дгарнин": 2})
            assert mark_session_outdated(db, chapter_id) is False
            assert db.get(ScriptChapter, chapter_id).session_outdated_at is None

    def test_an_outdated_chapter_with_gaps_waits(self, roots, monkeypatch):
        from app.services.chapter_delivery import mark_session_outdated
        with _sessions()() as db:
            chapter_id = self._archived(db, monkeypatch, roots)
            mark_session_outdated(db, chapter_id)
            db.commit()
            _finished(db, monkeypatch, missing_lines=1)

            result = chapter_delivery.archive_pending_chapter_sessions(db)

            assert result["rebuilt"] == 0
            assert db.get(ScriptChapter, chapter_id).session_outdated_at is not None
            assert len(list((roots / "nas").rglob("*.sesx"))) == 1

    def test_same_fingerprint_writes_nothing_and_clears_the_mark(self, roots, monkeypatch):
        from app.services.chapter_delivery import mark_session_outdated
        with _sessions()() as db:
            chapter_id = self._archived(db, monkeypatch, roots)
            mark_session_outdated(db, chapter_id)
            db.commit()
            letters = []
            monkeypatch.setattr(chapter_delivery, "send_telegram_message", lambda db, text, **k: letters.append(text) or 1)

            result = chapter_delivery.archive_pending_chapter_sessions(db)

            assert result["rebuilt"] == 0 and letters == []
            assert db.get(ScriptChapter, chapter_id).session_outdated_at is None
            assert len(list((roots / "nas").rglob("*.sesx"))) == 1

    def test_a_changed_session_keeps_the_old_file_and_tells_the_owner(self, roots, monkeypatch):
        from app.services.chapter_delivery import mark_session_outdated
        with _sessions()() as db:
            chapter_id = self._archived(db, monkeypatch, roots)
            chapter = db.get(ScriptChapter, chapter_id)
            chapter.session_sha256 = "другой"
            mark_session_outdated(db, chapter_id)
            db.commit()
            letters = []
            monkeypatch.setattr(chapter_delivery, "send_telegram_message", lambda db, text, **k: letters.append(text) or 1)

            result = chapter_delivery.archive_pending_chapter_sessions(db)
            db.commit()

            names = sorted(path.name for path in (roots / "nas").rglob("*.sesx"))
            assert result["rebuilt"] == 1
            assert len(names) == 2
            assert any(" до " in name for name in names)
            assert len(letters) == 1 and letters[0].startswith("🎬 Глава 5: сессия пересобрана.")
            assert db.get(ScriptChapter, chapter_id).session_outdated_at is None

    def test_a_taken_backup_name_gets_a_number(self, roots, monkeypatch):
        with _sessions()() as db:
            chapter_id = self._archived(db, monkeypatch, roots)
            chapter = db.get(ScriptChapter, chapter_id)
            for _ in range(2):
                # Одна и та же прежняя метка — одно и то же имя копии, второй нужен номер.
                chapter.session_archived_at = datetime(2026, 9, 15, 10, 42)
                chapter.session_sha256 = "другой"
                db.commit()
                archive_chapter_session(db, chapter_id)
                db.commit()
            names = [path.name for path in (roots / "nas").rglob("*.sesx")]
            assert len(names) == 3
            assert any(name.endswith(" (2).sesx") for name in names)

    def test_a_chapter_archived_before_fingerprints_keeps_its_old_file(self, roots, monkeypatch):
        with _sessions()() as db:
            chapter_id = self._archived(db, monkeypatch, roots)
            db.get(ScriptChapter, chapter_id).session_sha256 = ""
            db.commit()
            # Раскладка с тех пор поменялась: на NAS лежит другой проект.
            (session_file,) = list((roots / "nas").rglob("*.sesx"))
            session_file.write_text("<sesx>прежняя раскладка</sesx>", encoding="utf-8")

            result = archive_chapter_session(db, chapter_id)

            assert result["written"] is True and result["replaced"]
            assert len(list((roots / "nas").rglob("*.sesx"))) == 2

    def test_a_chapter_archived_before_fingerprints_with_the_same_layout_is_unchanged(self, roots, monkeypatch):
        from app.services.chapter_delivery import mark_session_outdated
        with _sessions()() as db:
            chapter_id = self._archived(db, monkeypatch, roots)
            chapter = db.get(ScriptChapter, chapter_id)
            digest = chapter.session_sha256
            chapter.session_sha256 = ""
            mark_session_outdated(db, chapter_id)
            db.commit()
            # Файл той поры мог прийти с BOM — отпечаток его не замечает.
            (session_file,) = list((roots / "nas").rglob("*.sesx"))
            session_file.write_bytes(b"\xef\xbb\xbf" + session_file.read_bytes())

            result = archive_chapter_session(db, chapter_id)

            assert result == {"written": False, "reason": "unchanged", "replaced": ""}
            chapter = db.get(ScriptChapter, chapter_id)
            assert chapter.session_sha256 == digest
            assert chapter.session_outdated_at is None
            assert len(list((roots / "nas").rglob("*.sesx"))) == 1


def test_the_backup_name_uses_ufa_time_not_utc(roots):
    """Имя копии читает монтажёр в Уфе: 10:42 UTC — это 15:42 у него на часах."""
    from datetime import datetime

    from app.services.chapter_delivery import _backup_key

    name = _backup_key("KP/Глава 5/KP_Ch05.sesx", datetime(2026, 9, 15, 10, 42))

    assert name == "KP/Глава 5/KP_Ch05 до 2026-09-15 15-42.sesx"
