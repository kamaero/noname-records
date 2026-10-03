from __future__ import annotations

import hashlib
import os

from app.models import AudioFile, ScriptChapter
from app.services import audio_storage
from app.services.audio_uploads import TAKE
from app.time_utils import utcnow_naive
# Импорт на верхнем уровне, а не внутри функции: тесты подменяют
# `audio_integrity.chapter_role_counts` через monkeypatch, а это работает только
# если `chapter_is_ready` читает имя как атрибут модуля, а не переимпортирует его
# заново при каждом вызове. Цикла тут нет — cast_ops про audio_integrity не знает.
from app.v2.cast_ops import chapter_role_counts

OK = "ok"
MISMATCH = "mismatch"
MISSING = "missing"
SKIPPED = "skipped"


def file_digest(path: str, chunk_size: int = 1024 * 1024) -> str:
    """md5 файла, прочитанного кусками.

    Гигабайтный дубль Рассказчика нельзя тянуть в память целиком — ни ради сверки,
    ни ради чего-либо ещё.
    """
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(db, item: AudioFile, *, nas_reachable: bool = True) -> str:
    """Перечитать файл там, где он лежит, и сравнить с сохранённой суммой.

    `location='nas'` встречается только у наследия до переезда приёма на локальный
    диск сервера — новые дубли лежат локально, и `nas_reachable` их не касается
    вовсе. Для строки на зеркале без живого NAS прочитать нечего: честный ответ —
    `skipped`, а не `missing` (файл никуда не делся, до него просто не достать
    сейчас); вердикт и `verified_at` не трогаем — когда зеркало оживёт, строка
    должна проверяться заново, как будто её ещё не трогали.
    """
    expected = str(item.md5 or "").strip()
    if not expected:
        # Записи старше миграции 0020: сверять не с чем. Считать их битыми — врать.
        return SKIPPED
    location = str(item.location or "local")
    if location == "nas" and not nas_reachable:
        return SKIPPED
    path = audio_storage.resolve_path(str(item.stored_key or ""), location=location)
    if not os.path.isfile(path):
        verdict = MISSING
    else:
        verdict = OK if file_digest(path) == expected else MISMATCH
    item.verify_state = verdict
    item.verified_at = utcnow_naive()
    db.add(item)
    return verdict


def chapter_files(db, chapter_id: str) -> list[AudioFile]:
    """Дубли главы. Пробы не в счёт: они не входят в сборку."""
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return []
    from app.services.chapter_delivery import _chapter_label

    return (
        db.query(AudioFile)
        .filter(AudioFile.kind == TAKE, AudioFile.chapter == _chapter_label(chapter))
        .order_by(AudioFile.role.asc(), AudioFile.uploaded_at.asc())
        .all()
    )


def chapter_ambient(db, chapter_id: str) -> list[AudioFile]:
    """Файлы готовых треков эмбиента главы — те, что ложатся в её архив и проект.

    Отдельно от `chapter_files`: там дубли, и по ним решается, записана ли глава.
    Трек эмбиента — не роль, но лежит в той же папке и едет в тот же архив, так
    что сверяется наравне с дублями.
    """
    from app.services.chapter_delivery import chapter_ambient_files

    return chapter_ambient_files(db, str(chapter_id or "").strip())


def files_to_verify(db, chapter_id: str) -> list[AudioFile]:
    """Всё, что сверка главы перечитывает: дубли и треки эмбиента."""
    return [*chapter_files(db, chapter_id), *chapter_ambient(db, chapter_id)]


def verify_chapter(db, chapter_id: str, *, nas_reachable: bool = True) -> dict | None:
    """Сверить все дубли и треки эмбиента главы. `None` — главы нет.

    Счёт — общий по всем файлам; эмбиент ещё и отдельной парой `ambient_*`: его
    чинит не диктор, а новая генерация, и экран говорит об этом отдельно.

    `nas_reachable` — это только про строки `location='nas'` (наследие до переезда
    на локальный диск): мёртвое зеркало метит их `skipped` и не касается остальных
    дублей главы, которые физически лежат на диске сервера.
    """
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return None
    tally = {OK: 0, MISMATCH: 0, MISSING: 0, SKIPPED: 0}
    takes = chapter_files(db, chapter_id)
    ambient = chapter_ambient(db, chapter_id)
    for item in takes:
        tally[verify_file(db, item, nas_reachable=nas_reachable)] += 1
    ambient_bad = 0
    for item in ambient:
        verdict = verify_file(db, item, nas_reachable=nas_reachable)
        tally[verdict] += 1
        ambient_bad += 1 if verdict in {MISMATCH, MISSING} else 0
    return {
        "checked": len(takes) + len(ambient),
        "ok": tally[OK],
        "mismatch": tally[MISMATCH],
        "missing": tally[MISSING],
        "skipped": tally[SKIPPED],
        "ambient_checked": len(ambient),
        "ambient_bad": ambient_bad,
    }


def chapter_is_ready(db, chapter_id: str) -> bool:
    """Все говорящие роли главы записаны хотя бы одним дублем."""
    # Роли считаются по действующим v2-атрибуциям, а не по тексту v1: на книге v2
    # список ролей иначе выходит пустым.
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return False
    roles = set(chapter_role_counts(db, chapter.id).keys())
    if not roles:
        return False
    recorded = {str(item.role or "").strip() for item in chapter_files(db, chapter_id)}
    return roles.issubset(recorded)


def verifiable_files(files: list[AudioFile]) -> list[AudioFile]:
    """Из уже прочитанных дублей — те, у которых есть хоть какая-то сумма.

    Сумма — из двух источников (см. комментарий у колонки `AudioFile.md5`). У
    большинства это сумма приёма: посчитана, когда диктор прислал файл, и `verify_file`
    для неё честно проверяет «файл такой, каким его записал диктор». У записей старше
    миграции 0020 суммы приёма не было — переезд в новую раскладку
    (scripts/migrate_audio_layout.py) досчитывает таким сумму задним числом (если он
    уже прошёл — полностью или для части записей: пока не прошёл, у них сумма
    по-прежнему пуста) — досчитанная сумма доказывает только «файл не менялся с
    момента переезда». `verify_file` для обоих источников отвечает одинаковым
    `ok`/`mismatch`, но означают они разное: это стоит помнить, читая
    `verify_state = ok` у записей старше миграции 0020.

    Функция от списка, а не второй запрос к таблице: `chapter_needs_verification`
    спрашивает `chapter_files` один раз и передаёт результат сюда.
    """
    return [item for item in files if str(item.md5 or "").strip()]


def chapter_needs_verification(db, chapter_id: str) -> bool:
    """Глава собрана, есть что сверять, и ни один её файл ещё не сверяли."""
    if not chapter_is_ready(db, chapter_id):
        return False
    files = chapter_files(db, chapter_id)
    # Дубли, принятые до миграции 0020, суммы приёма не имеют: `verify_file` честно
    # отвечает `skipped` и `verified_at` не пишет. Без этой проверки «ни один файл не
    # сверяли» оставалось правдой навсегда — каждая загрузка ставила в очередь `high`
    # пустое задание, которое отрабатывало за миллисекунды и ничего не меняло.
    if not verifiable_files(files):
        return False
    return all(item.verified_at is None for item in files)


def chapter_has_broken_files(db, chapter_id: str) -> bool:
    """Есть ли в главе файл, не прошедший сверку, — дубль или трек эмбиента."""
    return any(
        str(item.verify_state or "") in {MISMATCH, MISSING}
        for item in files_to_verify(db, chapter_id)
    )


def enqueue_verify_for_chapter(chapter_id: str) -> str | None:
    """Поставить главу в очередь на сверку. Повторный вызов не плодит заданий.

    Перечитывание идёт с локального диска сервера — то же место, куда приём кладёт
    новые дубли. Строки `location='nas'` (наследие до переезда) — исключение: их
    читать нечем без живого зеркала, и они честно остаются `skipped`. Отдельная
    очередь и фоновое выполнение — не из-за домашнего канала, а из-за размера
    главы: гигабайты сумм на воркер, а не на запрос из браузера.
    """
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    chapter_id = str(chapter_id or "").strip()
    if not chapter_id:
        return None
    return enqueue_tracked_task(
        queue_name="high",
        func_ref="app.worker_tasks.perform_verify_chapter_task",
        session_factory=SessionLocal,
        background_run_cls=BackgroundRun,
        job_kind="verify_chapter",
        entity_type="script_chapter",
        entity_id=chapter_id,
        run_key=f"verify-chapter-{chapter_id}",
        chapter_id=chapter_id,
    )
