"""Переполненный локальный диск не должен прилетать диктору как голый 500.

Приём файлов идёт только на локальный диск сервера (спул как отдельный режим
приёма упразднён — выбирать место больше не из чего), и забитый под ноль диск
не должен ронять ни сайт, ни воркер необработанным исключением. Честный отказ
(«сейчас не могу принять») дешевле диктору, чем непонятная ошибка вместо него.
Сторож места живёт в `audio_mirror.ensure_room`, и ручки ловят его
`audio_mirror.LocalDiskFull`.

Хранилище здесь не настоящее: `store_audio_file` заменён управляемой заглушкой,
которая поднимает `LocalDiskFull` по требованию теста. Сам сторож резерва уже
покрыт `tests/test_audio_mirror.py`; этот файл проверяет только то, что
HTTP-ручки корректно превращают это исключение в внятный ответ.
"""
import asyncio
import io
import json

from fastapi import UploadFile
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from types import SimpleNamespace

from app.api.recording import build_recording_handlers
from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services import audio_mirror
from app.time_utils import utcnow_naive


class _Request:
    headers: dict = {}


def _sessions():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _upload(name: str) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(b"RIFF0000"))


def _handlers(sessions, *, store_audio_file, sent=None):
    return build_recording_handlers({
        "is_authenticated": lambda request: True,
        "has_any_role": lambda request, roles: True,
        "has_workspace_full_access": lambda request: True,
        "session_payload": lambda request: {"display_name": "Актёр", "sub": "tg_1"},
        "is_agent": lambda request: False,
        "SessionLocal": sessions,
        "AudioFile": AudioFile,
        # По имени файла, а не константа: смешанная пачка (дубль + проба) проходит
        # проверку на дубликаты внутри пачки, и одинаковое каноническое имя у двух
        # разных файлов там же и споткнётся, заслонив собой то, что тестируется здесь.
        "build_canonical_audio_filename": lambda book_code, chapter, role, actor_name, original_filename, **kw: (
            original_filename or "audio.wav"
        ),
        "store_audio_file": store_audio_file,
        "send_telegram_message": lambda db, text: (sent.append(text) if sent is not None else None),
        "recording_identity": lambda request, name: ("u1", name),
        "mark_role_progress_recorded": lambda *args: 0,
        "recording_workspace_payload": lambda db, **kwargs: {},
        "require_recording_access": lambda request: None,
        "parse_batch_audio_filename": lambda filename, default_book_code="", default_actor_name="": {
            "filename_chapter_index": 0,
        },
    })


def _always_full(db, **kwargs):
    raise audio_mirror.LocalDiskFull("local_disk_full")


class TestASingleTakeArrivesWhileTheDiskIsFull:
    def test_the_replica_patch_endpoint_answers_instead_of_crashing(self):
        sessions = _sessions()
        handler = _handlers(sessions, store_audio_file=_always_full)["recording_replica_patch"]

        response = asyncio.run(handler(
            _Request(), book_code="КП", chapter="Глава1", role="Роль",
            actor_name="Актёр", line_index=3, file=_upload("p.wav"),
        ))

        assert response.status_code == 507
        body = json.loads(response.body)
        assert body["ok"] is False
        assert body["error"] == "disk_full"


class TestABatchWhereOnlyOneFileOverflowsTheDisk:
    """Один файл не влез — это его находка, а не приговор всей пачке.

    То, что уже сохранилось и закоммичено, не должно тонуть в тишине вместе с
    отказом: уведомление и постановка ASR идут по сохранённому подмножеству,
    а не глушатся целиком из-за соседнего файла, которому не хватило места.
    `enqueue_asr_for_chapter` во всём проекте зовётся ровно из этой ручки — ни
    планировщика, ни повторного триггера нет, — так что пропущенный вызов
    здесь значит: распознавание для уже записанного файла не запустится
    никогда, пока кто-то не загрузит его заново.
    """

    def _store(self, db, *, original_filename, book_code, chapter, role, actor_name, kind, **_kwargs):
        if original_filename == "too_big.wav":
            raise audio_mirror.LocalDiskFull("local_disk_full")
        return SimpleNamespace(
            id=f"id-{original_filename}",
            original_filename=original_filename,
            canonical_filename=original_filename,
            chapter=chapter,
            role=role,
            actor_name=actor_name,
            kind=kind,
        )

    def _seed_take(self, sessions) -> None:
        """Книга, глава и утверждение роли — чтобы «fits.wav» классифицировался
        как дубль (а не проба) и нашлась его глава для постановки ASR."""
        with sessions() as db:
            book = ScriptBook(
                title="Крылья полумрака", source_filename="k.txt",
                source_format="txt", created_at=utcnow_naive(),
            )
            db.add(book)
            db.flush()
            db.add(ScriptChapter(book_id=book.id, chapter_index=1, chapter_title="Глава1", status="published"))
            db.add(Character(book_id=book.id, name="Дгарнин", actor_name="Актёр"))
            db.commit()

    def test_the_other_file_still_gets_saved_notified_and_queued_for_asr(self, monkeypatch):
        sessions = _sessions()
        self._seed_take(sessions)
        sent: list = []
        queued: list = []
        monkeypatch.setattr("app.api.recording.enqueue_asr_for_chapter", lambda chapter_id: queued.append(chapter_id))
        handler = _handlers(sessions, store_audio_file=self._store, sent=sent)["recording_batch"]
        files = [_upload("too_big.wav"), _upload("fits.wav")]
        meta = [
            {"book_code": "КП", "chapter": "", "role": "Сатухух", "actor_name": "Актёр"},
            {"book_code": "КП", "chapter": "Глава1", "role": "Дгарнин", "actor_name": "Актёр"},
        ]

        response = asyncio.run(handler(_Request(), files=files, meta_json=json.dumps(meta)))

        assert response.status_code == 507
        body = json.loads(response.body)
        assert body["ok"] is False
        assert body["error"] == "disk_full"
        assert [row["file"] for row in body["items"]] == ["too_big.wav"]
        assert body["saved_count"] == 1
        assert [row["original_filename"] for row in body["saved"]] == ["fits.wav"]
        # Файл, который упёрся в переполненный диск, не топит уведомление и
        # распознавание того, что реально сохранилось и уже закоммичено.
        assert len(sent) == 1
        assert len(queued) == 1

    def test_a_broken_queue_after_saving_does_not_turn_the_answer_into_a_500(self, monkeypatch):
        """Redis отвалился после того, как файлы сохранены и закоммичены.

        Постановка распознавания идёт последней, уже за пределами транзакции: её сбой
        не отменяет сохранённого, но необработанным исключением превращал ответ в 500 —
        и диктор видел «файлы не загружены» про файлы, которые лежат на месте.
        Распознавание запустят заново; переспрошенная загрузка стоит гигабайта трафика.
        """
        sessions = _sessions()
        self._seed_take(sessions)

        def _explode(chapter_id):
            raise RuntimeError("redis is gone")

        monkeypatch.setattr("app.api.recording.enqueue_asr_for_chapter", _explode)
        handler = _handlers(sessions, store_audio_file=self._store)["recording_batch"]
        meta = [{"book_code": "КП", "chapter": "Глава1", "role": "Дгарнин", "actor_name": "Актёр"}]

        response = asyncio.run(handler(_Request(), files=[_upload("fits.wav")], meta_json=json.dumps(meta)))

        assert response.status_code == 200
        body = json.loads(response.body)
        assert body["ok"] is True and body["saved_count"] == 1
