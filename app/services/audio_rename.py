"""Переименовали человека — переименовалось и в его записях.

Имя актёра в `audio_files` — это имя учётки на момент загрузки: оно замораживается, а
учётку потом переименовывают или сливают, и файл начинает говорить о человеке, которого
в системе больше нет.

Само по себе это косметика. Но по тому же имени решается, дубль перед нами или проба:
роль за тобой — дубль, чужая — проба. Диктор загрузил свою роль под ником, а в касте
он записан по фамилии; сверка их не связала — и записи роли, на которую он утверждён,
легли пробами.

Поэтому имя переносится вместе с учёткой, а вид записи и её каноническое имя
пересчитываются заново — и файл на диске переезжает под новое имя. Без этого папка
главы в зеркале говорила о прежнем человеке, а сайт — о новом.
"""
from __future__ import annotations

import logging
import os

from sqlalchemy import event

from app.models import AudioFile, PendingMirrorDeletion
from app.services import audio_storage
from app.services.audio_mirror import storage_key
from app.services.audio_naming import canonical_audio_name, name_suffixes
from app.services.audio_uploads import storage_file_name
from app.services.shared_runtime import safe_name
from app.services.upload_kind import classify_upload_kind

logger = logging.getLogger(__name__)


def _undo_moves_on_rollback(db, moves: list[tuple[str, str]]) -> None:
    """Файл переезжает до коммита: откатилась база — файл возвращается на место.

    Иначе строка с прежним ключом указала бы в пустоту. После коммита откат другой
    работы в той же сессии переезд уже не трогает.
    """
    def undo(session) -> None:
        event.remove(db, "after_commit", forget)
        for old_key, new_key in reversed(moves):
            try:
                audio_storage.move_file(new_key, old_key)
            except OSError:
                logger.exception("Не удалось вернуть %s на место %s после отката", new_key, old_key)

    def forget(session) -> None:
        event.remove(db, "after_rollback", undo)

    event.listen(db, "after_rollback", undo, once=True)
    event.listen(db, "after_commit", forget, once=True)


def _move_to_name(db, item: AudioFile, name: str) -> tuple[str, str] | None:
    """Переложить локальный файл под новое имя. `None` — файл остался где был.

    Трогается только локальный диск: старая запись, чья единственная копия на NAS,
    ждёт переезда на локальный диск, а из обработчика запроса к NAS не ходим —
    мёртвое монтирование держит поток. Её строка по-прежнему указывает на свой файл.
    """
    if str(item.location or "local") != "local":
        return None
    old_key = str(item.stored_key or "")
    new_key = storage_key(str(item.book_code or ""), str(item.chapter or ""),
                          storage_file_name(name, safe_name), str(item.kind or ""))
    if not old_key or new_key == old_key:
        return None
    try:
        if not os.path.isfile(audio_storage.resolve_path(old_key)):
            return None
        # Под новым именем уже что-то лежит — чужой файл или сирота: не затираем.
        if os.path.exists(audio_storage.resolve_path(new_key)):
            return None
        if db.query(AudioFile.id).filter(AudioFile.stored_key == new_key, AudioFile.id != item.id).first():
            return None
        audio_storage.move_file(old_key, new_key)
    except (OSError, RuntimeError, ValueError):
        logger.exception("Файл %s не переехал под имя %s — остаётся под прежним", old_key, name)
        return None
    item.stored_key = new_key
    if item.mirrored_at is not None:
        # Копия на NAS лежит под прежним ключом. Новую сделает зеркало, прежнюю
        # сотрёт его же уборка — из запроса NAS не трогаем.
        db.add(PendingMirrorDeletion(stored_key=old_key, book_code=str(item.book_code or ""),
                                     deleted_by="rename"))
        item.mirrored_at = None
        item.mirror_state = ""
    return old_key, new_key


def rename_actor_in_audio(db, *, was: str, now: str) -> int:
    """Сменить имя актёра во всех его записях. Возвращает, сколько строк изменилось.

    Заодно пересчитывается вид записи: под новым именем роль может оказаться своей.
    Отметка о загрузке не трогается — это отметка о событии, а не о правке.
    """
    was, now = " ".join(str(was or "").split()), " ".join(str(now or "").split())
    if not was or not now or was == now:
        return 0

    # Имена, которые уже носит новое имя актёра (при слиянии учёток у него могут быть свои
    # записи), — и те, что раздаются в этом проходе: два файла не должны сойтись в одно имя.
    taken = {
        str(name or "")
        for (name,) in db.query(AudioFile.canonical_filename).filter(AudioFile.actor_name == now).all()
    }
    changed = 0
    moves: list[tuple[str, str]] = []
    for item in db.query(AudioFile).filter(AudioFile.actor_name == was).order_by(AudioFile.uploaded_at.asc()).all():
        kind = classify_upload_kind(db, role=str(item.role or ""), actor_name=now,
                                    book_code=str(item.book_code or ""))
        # Номер фикса и порядковый номер переносятся из прежнего имени: без них фикс
        # и основной файл той же роли после переименования назывались бы одинаково.
        fix, ordinal = name_suffixes(str(item.canonical_filename or ""))
        if kind == "audition":
            fix = None
        while True:
            name = canonical_audio_name(
                book_code=str(item.book_code or ""), chapter=str(item.chapter or ""),
                role=str(item.role or ""), actor_name=now,
                original_filename=str(item.original_filename or ""), kind=kind, ordinal=ordinal, fix=fix,
            )
            if name not in taken:
                break
            if fix is not None:
                fix += 1
            else:
                ordinal += 1
        taken.add(name)
        item.actor_name = now
        item.kind = kind
        item.canonical_filename = name
        moved = _move_to_name(db, item, name)
        if moved:
            moves.append(moved)
        changed += 1
    if moves:
        _undo_moves_on_rollback(db, moves)
    return changed


def rename_audition_role(db, *, book, was: str, now: str, actor_name: str) -> int:
    """Пробы актёра под ошибочным написанием роли («Лйра») — под написание книги («Лира»).

    Роль пробы диктор пишет на слух в имени файла. Владелец не хочет, чтобы его ошибка
    становилась вторым именем роли в касте (13.10), — поэтому правится проба, а не каст:
    роль, каноническое имя и файл на диске, а с ними оценки и отказ пары (их ключ —
    роль и актёр). Вид записи остаётся «проба»: пересчёт по касту сделал бы её дублем,
    если актёр уже утверждён на роль.

    Если у пары под верным написанием оценка той же дорожки или отказ уже есть, они
    остаются, а перенесённые уходят: ключ пары один, а голос там — о пробе, присланной
    позже. Не коммитит.
    """
    from app.models import AuditionRejection, AuditionScore
    from app.services.audio_uploads import AUDITION, derive_book_code

    was, now = " ".join(str(was or "").split()), " ".join(str(now or "").split())
    if not was or not now or was == now:
        return 0
    book_code = derive_book_code(str(book.title or ""))
    taken = {str(name or "") for (name,) in db.query(AudioFile.canonical_filename).all()}
    moves: list[tuple[str, str]] = []
    items = (db.query(AudioFile)
             .filter(AudioFile.kind == AUDITION, AudioFile.book_code == book_code,
                     AudioFile.role == was, AudioFile.actor_name == actor_name)
             .order_by(AudioFile.uploaded_at.asc()).all())
    for item in items:
        taken.discard(str(item.canonical_filename or ""))
        _fix, ordinal = name_suffixes(str(item.canonical_filename or ""))
        while True:
            name = canonical_audio_name(
                book_code=book_code, chapter=str(item.chapter or ""), role=now, actor_name=actor_name,
                original_filename=str(item.original_filename or ""), kind=AUDITION, ordinal=ordinal, fix=None,
            )
            if name not in taken:
                break
            ordinal += 1
        taken.add(name)
        item.role = now
        item.canonical_filename = name
        moved = _move_to_name(db, item, name)
        if moved:
            moves.append(moved)
    if moves:
        _undo_moves_on_rollback(db, moves)

    lanes = {str(lane) for (lane,) in db.query(AuditionScore.lane).filter(
        AuditionScore.book_id == book.id, AuditionScore.role == now, AuditionScore.actor_name == actor_name)}
    for score in db.query(AuditionScore).filter(
            AuditionScore.book_id == book.id, AuditionScore.role == was, AuditionScore.actor_name == actor_name):
        if str(score.lane) in lanes:
            db.delete(score)
        else:
            score.role = now
    has_rejection = db.query(AuditionRejection.id).filter_by(
        book_id=book.id, role=now, actor_name=actor_name).first() is not None
    for rej in db.query(AuditionRejection).filter_by(book_id=book.id, role=was, actor_name=actor_name):
        if has_rejection:
            db.delete(rej)
        else:
            rej.role = now
    db.flush()
    return len(items)
