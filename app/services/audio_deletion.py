"""Убрать загруженную запись — из базы, с локального диска и, следом, с зеркала.

Порядок внутри — два коммита, и оба здесь, в одной функции, а не разнесены между
сервисом и вызывающим: `os.remove` необратим, а `AudioFile`/`AsrJob`/
`PendingMirrorDeletion` живут в той же базе, что и журнал вмешательств — значит,
пока не закоммичено, всё это можно откатить, и стирать файл раньше, чем это
случится, — раньше времени.

Настоящий порядок:

1. Запись в журнал вмешательств — коммит.
2. Удаление строки `audio_files`, её `asr_jobs`, поручение зеркалу — коммит.
3. И только теперь `os.remove`: к этому моменту в базе уже согласованно — записи
   для системы больше не существует ни в одном её собственном представлении, а
   значит, что бы дальше ни случилось с файлом на диске, это уже не расхождение
   между базой и диском, а самостоятельная, безопасная утечка байтов.

Если сбой случится между двумя коммитами (второй бросит исключение), первый уже
необратим — журнал останется. Но это не расхождение: строка `audio_files` тоже
осталась (второй коммит не состоялся), файл на диске тоже цел (`os.remove` ещё
не звался) — система согласованна, просто операция не завершилась и её можно
повторить. Опасная асимметрия была бы только если бы `os.remove` стоял до
второго коммита или после него в другом, уже некоммитящем вызывающем, — поэтому
ни сервис, ни ручка над ним такого шва не оставляют: коммитит только эта
функция, и оба коммита идут раньше необратимого действия.

NAS здесь не трогается вовсе. Мёртвое `hard`-монтирование не отдаёт ошибку, а
виснет навсегда, и `try` этого не ловит: обработчик запроса повис бы, съев поток.
Копию на зеркале стирает фоновый цикл по поручению, оставленному здесь.
"""
from __future__ import annotations

import logging
import os

from app.time_utils import iso_utc

logger = logging.getLogger(__name__)

#: Диктору можно удалить свою запись только в течение суток после загрузки.
#: Не час: записывают вечером, а замечают утром.
DICTOR_DELETE_WINDOW_HOURS = 24


class AudioDeleteError(RuntimeError):
    """`code` — машинный код отказа для экрана, не фраза для человека."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _find_book_for_audio(db, audio):
    """Книга, к которой относится запись — та же связь, что у `find_chapter_for_take`.

    `audio_files.book_code` хранит код, выведенный из заголовка книги, а не её
    идентификатор — другого ключа у файла нет.
    """
    from app.models import ScriptBook
    from app.services.audio_naming import book_token
    from app.services.audio_uploads import derive_book_code

    wanted = book_token(str(getattr(audio, "book_code", "") or ""))
    if not wanted:
        return None
    for book in db.query(ScriptBook).all():
        if book_token(derive_book_code(str(book.title or ""))) == wanted:
            return book
    return None


def delete_audio_file(
    db, audio_id: str, *, actor_uid: str, actor_name: str, privileged: bool, is_agent: bool = False,
) -> dict:
    """Стереть запись насовсем. Возвращает то, что экран покажет в подтверждении.

    Порядок: след в журнале (коммит) -> строка `audio_files`, её `asr_jobs` и
    поручение зеркалу (коммит) -> и только теперь локальный файл. Оба коммита —
    здесь; вызывающий (ручка) получает уже полностью согласованный результат и
    больше ничего не коммитит.

    Агент грузит записи за актёров, но не удаляет — ни чужие, ни собственные
    свежие загрузки: `is_agent` отказывает раньше `names_match`, иначе отказ
    выходил бы случайным совпадением имён, а не явным правилом.
    """
    from app.models import AsrJob, AudioFile, PendingMirrorDeletion
    from app.services import audio_storage
    from app.services.audio_uploads import AUDITION
    from app.services.telegram import names_match
    from app.time_utils import utcnow_naive
    from app.v2.store import record_operator_intervention

    audio = db.get(AudioFile, audio_id)
    if audio is None:
        raise AudioDeleteError("not_found")

    # Агент грузит за актёров, но не стирает. Проверка стоит раньше `names_match`
    # намеренно: без неё отказ выходил бы случайным — файл, загруженный за другого,
    # подписан другим, — а собственную загрузку агент стёр бы.
    if is_agent and not privileged:
        raise AudioDeleteError("forbidden_agent")

    if not privileged:
        if not names_match(str(audio.actor_name or ""), str(actor_name or "")):
            raise AudioDeleteError("forbidden")
        age = utcnow_naive() - audio.uploaded_at
        if age.total_seconds() > DICTOR_DELETE_WINDOW_HOURS * 3600:
            raise AudioDeleteError("too_old")

    result = {
        "role": audio.role,
        "chapter": audio.chapter,
        "kind": audio.kind,
        "canonical_filename": audio.canonical_filename,
        "size_bytes": audio.size_bytes,
    }
    stored_key = audio.stored_key
    book_code = audio.book_code

    # 1. Сначала след — если что-то дальше пойдёт не так, видно, что случилось.
    book = _find_book_for_audio(db, audio)
    # Считаем ДО удаления ниже: после второго коммита спросить будет уже не у кого,
    # а собственного следа удаление заданий распознавания не оставляет нигде.
    asr_jobs_deleted = db.query(AsrJob).filter(AsrJob.audio_file_id == audio.id).count()
    record_operator_intervention(
        db,
        book=book,
        action_type="audio_delete",
        actor_uid=actor_uid,
        actor_name=actor_name,
        payload={
            # Идентификатор самой записи и код книги. `record_operator_intervention`
            # кладёт в строку `book_id` только когда книга нашлась по коду, а не
            # найтись она может законно (`_find_book_for_audio` возвращает None, и
            # это поддержанная ветка) — тогда без этих двух полей про удалённое
            # остаётся один заголовок главы строкой, а связать его с артефактом,
            # где записан идентификатор записи, нечем вовсе.
            "audio_id": audio.id,
            "book_code": book_code,
            "role": audio.role,
            "chapter": audio.chapter,
            "kind": audio.kind,
            "original_filename": audio.original_filename,
            "canonical_filename": audio.canonical_filename,
            "stored_key": stored_key,
            "size_bytes": audio.size_bytes,
            "md5": audio.md5,
            "duration_seconds": audio.duration_seconds,
            # После коммита ниже строки `audio_files` не останется — этот payload
            # единственный след того, чья была запись и когда её загрузили.
            # Без этих двух полей атрибуция опиралась бы только на разбор
            # `canonical_filename`/`original_filename` по соглашению об
            # именовании, которое ничем не проверяется и однажды не выполнится
            # (опечатка, ручная загрузка за диктора, смена конвенции) —
            # и уже удалённой записи задним числом эти поля не проставишь.
            #
            # НЕ путать с `actor_name`/`actor_uid` самой строки журнала (см.
            # аргументы `record_operator_intervention` ниже) — те называют
            # того, кто НАЖАЛ «удалить». `uploaded_by`/`uploaded_at` — про
            # другого человека и другой момент: кто записал звук и когда его
            # загрузили. Когда удаляет владелец за диктора, это два разных
            # имени и это единственное место, где разница видна.
            "uploaded_by": audio.actor_name,
            "uploaded_at": iso_utc(audio.uploaded_at),
            # Задания распознавания уходят вместе с записью вторым коммитом и
            # молча. Их число здесь — единственное, что потом скажет, была ли у
            # записи расшифровка и сколько попыток за ней стояло.
            "asr_jobs_deleted": asr_jobs_deleted,
        },
    )
    # Коммит журнала отдельно: он необратим по смыслу (свидетельство того, что
    # операция началась) и должен пережить сбой второго коммита ниже.
    db.commit()

    # 2. Строка и её задания распознавания — вместе с поручением зеркалу, одним
    # коммитом. Если он не состоится, откатится всё вместе: строка, задания,
    # поручение — а `os.remove` ещё не звался, файл цел. Система остаётся
    # согласованной, просто операция не завершилась.
    db.query(AsrJob).filter(AsrJob.audio_file_id == audio.id).delete()
    if str(audio.kind or "") == AUDITION:
        # Локальный импорт: `audition_reactions` сам импортирует этот модуль.
        from app.services.audition_reactions import forget_audition

        # Реакции и отказ — в том же коммите, что и строка: иначе 👎 удалённой пробы
        # продолжал бы держать отказ, и письмо ушло бы про пробу, которой уже нет.
        forget_audition(db, audio)
    db.delete(audio)
    db.add(PendingMirrorDeletion(stored_key=stored_key, book_code=book_code, deleted_by=str(actor_name or "")))
    db.commit()

    # 3. Локальный файл — последним и только теперь: в базе уже согласованно
    # (строки нет, поручение зеркалу есть), значит стирание байтов на диске уже
    # ничем не рискует. Пропавший или неудалившийся файл не отменяет операцию —
    # цель в том, чтобы система перестала считать запись существующей, а не в
    # том, чтобы гарантированно стереть байты; на них к этому моменту никто не
    # ссылается, и `PendingMirrorDeletion` в любом случае даст фоновому циклу
    # ещё одну попытку добраться до NAS-копии.
    try:
        os.remove(audio_storage.resolve_path(stored_key))
    except OSError:
        logger.exception(
            "Не удалось стереть локальный файл %s после удаления записи %s — строка уже "
            "убрана из базы, файл остаётся безобидным мусором на диске",
            stored_key, audio_id,
        )

    return result
