from __future__ import annotations

import logging
import os
import re

from app.models import AudioFile, PendingMirrorDeletion
from app.services import audio_storage, nas_health
from app.services.audio_naming import book_token
from app.time_utils import utcnow_naive

logger = logging.getLogger(__name__)

OK = "ok"
MISMATCH = "mismatch"


class LocalDiskFull(Exception):
    """Локального места нет — принимать файл некуда."""


def ensure_room(size_hint: int, *, free: int, reserve_bytes: int) -> None:
    """Пустить файл, только если после него останется резерв.

    Раньше эта проверка стерегла отдельный локальный буфер, куда уходили файлы на
    время, пока молчал NAS. Тот буфер упразднён: диск сервера — единственное место
    приёма, и забитый под ноль диск роняет и сайт, и воркер, поэтому сторож теперь
    здесь.
    """
    if int(free) - max(0, int(size_hint)) < int(reserve_bytes):
        raise LocalDiskFull("local_disk_full")


#: «Глава 5. Господа мухоморы» → 5. Номер берётся из начала заголовка, где его
#: ставит пайплайн; всё остальное в заголовке для пути не нужно.
_CHAPTER_NUMBER = re.compile(r"(\d+)")

AUDITIONS_FOLDER = "Auditions"


def chapter_folder(chapter: str) -> str:
    """`Глава 5. …` → `Chapter05`.

    Номер дополняется нулём: без этого шестьдесят глав в файловом менеджере
    встают как 1, 10, 11, 2 — а папки открывают руками, ради этого всё и затеяно.
    Заголовок без числа даёт `Chapter00`: приём не должен падать из-за названия,
    а такую папку видно глазами и можно разложить потом.
    """
    found = _CHAPTER_NUMBER.search(str(chapter or ""))
    number = int(found.group(1)) if found else 0
    return f"Chapter{number:02d}"


def storage_key(book_code: str, chapter: str, canonical_filename: str, kind: str) -> str:
    """Относительный путь файла — один и тот же на сервере и на NAS.

    Различает их только корень, поэтому один ключ адресует обе копии, и
    зеркалированию не нужно ничего пересчитывать.
    """
    # `book_code` приезжает полем формы из браузера, а здесь становится сегментом
    # пути. Тот же токен, что собирает префикс имени файла, оставляет только латиницу
    # и цифры: разделителю и «..» взяться неоткуда, а папка `KP/` совпадает с именем
    # `KP_Ch01_…` — иначе кириллическое «КП» развело бы одну книгу по двум деревьям.
    book = book_token(book_code) or "BOOK"
    folder = AUDITIONS_FOLDER if str(kind or "") == "audition" else chapter_folder(chapter)
    return f"{book}/{folder}/{canonical_filename}"


def roots_overlap() -> bool:
    """Один и тот же диск под обеими копиями — то же самое, что одна копия.

    Так выйдет, если выкатить ветку и не поправить конфиг: `AUDIO_STORAGE_PATH`
    останется указывать на монтирование NAS, а корень зеркала окажется внутри него.
    Цикл бодро отчитается, что всё скопировано, на экране загорится зелёная галочка,
    и владелец прочитает её как «две копии есть, сервер можно чистить» — имея одну.
    Врущая галочка опаснее, чем стоящий цикл.

    Пустой корень зеркала — не перекрытие, а законное «зеркала нет»: этот случай
    цикл разбирает отдельно и тоже молча стоит.
    """
    local = os.path.realpath(audio_storage.local_root())
    nas = (audio_storage.nas_root() or "").strip()
    if not nas:
        return False
    nas = os.path.realpath(nas)
    return local == nas or nas.startswith(local + os.sep) or local.startswith(nas + os.sep)


def mirror_once(db) -> dict:
    """Скопировать на NAS всё, у чего ещё нет подтверждённой второй копии.

    Порядок на один файл: записать на NAS → перечитать оттуда → сверить сумму
    с посчитанной при приёме → обновить строку → закоммитить. Локальная копия
    не трогается: смысл зеркала в том, что копий две.

    Коммит на каждый файл, а не один на весь проход: исключение на файле N
    иначе откатило бы отметки по файлам 1..N-1, чьи копии на NAS уже лежат,
    и следующий круг переписал бы их заново.
    """
    from app.services.asr_run import find_chapter_for_take
    from app.services.audio_integrity import file_digest
    from app.services.integrity_notice import notify_mirror_problem

    if not audio_storage.nas_root() or nas_health.nas_online() is not True:
        return {"mirrored": 0, "failed": 0, "left": 0}
    if roots_overlap():
        logger.error(
            "Корни хранилища перекрываются: локальный %s, зеркало %s. Обе копии легли бы "
            "на один диск, а отметка сказала бы, что их две. Зеркалирование стоит, пока "
            "настройки не разведены.",
            audio_storage.local_root(), audio_storage.nas_root(),
        )
        return {"mirrored": 0, "failed": 0, "left": 0}

    from app.services.mirror_evidence import make_mirror_proof, mirror_proof_valid

    mirrored = failed = processed = 0
    candidates = db.query(AudioFile).filter(AudioFile.mirror_state != MISMATCH).all()
    for item in candidates:
        if (item.mirrored_at is not None and item.mirror_state == OK
                and mirror_proof_valid(item, root=audio_storage.nas_root())):
            continue
        if processed >= 50:
            break
        key = str(item.stored_key or "")
        local = audio_storage.resolve_path(key)
        if not os.path.isfile(local):
            continue
        processed += 1
        identity = (str(item.id), key, str(item.md5 or ""), int(item.size_bytes or 0))
        try:
            actual_md5, actual_size = file_digest(local), os.path.getsize(local)
            landed_ok = (actual_size == identity[3] and actual_size > 0
                         and (not identity[2] or actual_md5 == identity[2]))
            if landed_ok:
                nas_path = audio_storage.resolve_path(key, location="nas")
                if item.mirrored_at is None or not os.path.isfile(nas_path):
                    with open(local, "rb") as source:
                        audio_storage.write_stream(key, source, location="nas")
                landed_ok = file_digest(nas_path) == actual_md5 and os.path.getsize(nas_path) == actual_size
        except OSError:
            item.mirror_state = "error"
            db.commit()
            failed += 1
            continue
        # Байты могли копироваться минуты. Обновление ключа или checksum за это
        # время лишает квитанцию адресата; служебные поля на неё не влияют.
        db.expire(item)
        if (str(item.id), str(item.stored_key or ""), str(item.md5 or ""), int(item.size_bytes or 0)) != identity:
            failed += 1
            continue
        if landed_ok:
            # Старые исходники без суммы получают проверенный текущий baseline
            # только после совпадения локальных и NAS-байтов, а не из миграции.
            item.md5 = actual_md5
            item.mirror_state = OK
            item.mirrored_at = utcnow_naive()
            item.mirror_proof_json = make_mirror_proof(item, checksum=actual_md5, size=actual_size,
                                                     root=audio_storage.nas_root())
            db.add(item)
            db.commit()
            mirrored += 1
        else:
            item.mirror_state = MISMATCH
            db.add(item)
            db.commit()
            chapter = find_chapter_for_take(db, item)
            if chapter is not None:
                notify_mirror_problem(db, chapter.id)
            failed += 1
    left = 0
    for item in db.query(AudioFile).filter(AudioFile.mirror_state != MISMATCH):
        if not mirror_proof_valid(item, root=audio_storage.nas_root()):
            if os.path.isfile(audio_storage.resolve_path(str(item.stored_key or ""))):
                left += 1
    return {"mirrored": mirrored, "failed": failed, "left": left}


def purge_deleted_once(db) -> dict:
    """Снять поручения `PendingMirrorDeletion`, стерев соответствующие копии на NAS.

    Строка `audio_files` к этому моменту уже удалена локальным путём
    (`audio_deletion.delete_audio_file`), и NAS в нём нарочно не трогали: мёртвое
    `hard`-монтирование не отдаёт ошибку, а виснет навсегда, и обработчик запроса
    съел бы поток. Поручение — единственный оставшийся след того, что стереть
    предстоит; разбирает его этот проход, за теми же ограждениями, что и
    `mirror_once`.

    Относительный ключ хранения один и тот же под обоими корнями (см.
    `storage_key`), поэтому `resolve_path(key, location="nas")` адресует ровно ту
    копию, что скопировал `mirror_once` — включая легаси-строки, у которых
    `location == "nas"` и локальной копии никогда не было вовсе: для них поручение
    оставляет единственный путь до настоящего удаления.

    Коммит на каждое поручение, а не один на весь проход: исключение на
    поручении N иначе откатило бы снятие поручений 1..N-1, чьи копии на NAS уже
    стёрты, — и следующий круг искал бы их заново, хотя стирать уже нечего.

    Поручение, чей ключ к этому моменту снова занят живой строкой `audio_files`,
    снимается НЕ исполняясь. `next_ordinal` считает живые строки той же роли,
    главы и актёра, поэтому удаление освобождает номер, и следующая загрузка
    получает то же каноническое имя, а с ним и тот же `stored_key`: поручение
    начинает указывать на копию не удалённой записи, а новой. Исполнить его
    значило бы стереть зеркало свежего файла — и молча, потому что `mirror_once`
    берёт только строки с `mirrored_at IS NULL` и второй раз не скопирует
    никогда, а копию на NAS не сверяет никто. Осталась бы вечная галочка «копий
    две» при одной копии, и это в единственной избыточности, какая у записей
    есть. Ровно так выглядит «удалил не тот дубль, залил правильный» — то, ради
    чего кнопка удаления и написана.
    """
    pending_count = lambda: db.query(PendingMirrorDeletion).count()  # noqa: E731

    if not audio_storage.nas_root() or nas_health.nas_online() is not True:
        return {"purged": 0, "missing": 0, "reused": 0, "left": pending_count()}
    if roots_overlap():
        logger.error(
            "Корни хранилища перекрываются: локальный %s, зеркало %s. Стирание "
            "поручений остановлено — на одном диске лежала бы единственная копия, а "
            "её пропажа выглядела бы как обычная уборка мусора.",
            audio_storage.local_root(), audio_storage.nas_root(),
        )
        return {"purged": 0, "missing": 0, "reused": 0, "left": pending_count()}

    purged = missing = reused = 0
    for item in db.query(PendingMirrorDeletion).all():
        key = str(item.stored_key or "")
        if key and db.query(AudioFile).filter(AudioFile.stored_key == key).first() is not None:
            # Не ошибка и не повод оставлять поручение висеть: стирать нечего,
            # по этому ключу теперь лежит копия другой, живой записи.
            logger.info(
                "Поручение %s на стирание %s снято не исполняясь: ключ снова занят живой "
                "записью — файл перезалили после удаления, и на зеркале лежит уже её копия",
                item.id, key,
            )
            db.delete(item)
            db.commit()
            reused += 1
            continue
        path = audio_storage.resolve_path(key, location="nas")
        try:
            existed = os.path.isfile(path)
            if existed:
                os.remove(path)
        except OSError:
            logger.exception(
                "Не удалось стереть копию на NAS %s по поручению %s — поручение "
                "остаётся, следующий круг попробует снова",
                key, item.id,
            )
            continue
        db.delete(item)
        db.commit()
        if existed:
            purged += 1
        else:
            missing += 1
    return {"purged": purged, "missing": missing, "reused": reused, "left": pending_count()}
