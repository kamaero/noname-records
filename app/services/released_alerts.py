"""Тревоги владельцу о том, что случилось в уже сданной главе.

Глава «сдана» (`ScriptChapter.released_at`) — владелец принял её и больше не ждёт
правок. Но дубль диктора, рекаст роли или новые пропуски сверки в такой главе молча
меняют то, что он уже принял. Сессию сданной главы эти уведомления не трогают
(`mark_session_outdated` не зовём): решать, пересобирать ли её, должен владелец.

Отправка никогда не роняет вызывающего: загрузка, пересчёт и сверка сроков важнее
письма, поэтому сбой Telegram только пишется в журнал и возвращается как «не ушло».
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from app.config import settings
from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services.audio_uploads import book_code_matches_title

logger = logging.getLogger(__name__)

def _take_link(audio_id) -> str:
    """Ссылка на дубль от `APP_BASE_URL`; без неё в письме остаётся одно имя файла."""
    base = (settings.app_base_url or "").strip().rstrip("/")
    return f"{base}/api/v2/takes/{audio_id}/audio" if base else ""


def _default_send(db) -> Callable[[str], object]:
    from app.services.telegram import send_telegram_message

    return lambda text: send_telegram_message(db, text)


def _deliver(db, text: str, send) -> bool:
    try:
        (send or _default_send(db))(text)
        return True
    except Exception:
        logger.exception("Не удалось сообщить владельцу о событии в сданной главе")
        return False


def _released_chapter_for(db, audio: AudioFile) -> tuple[ScriptBook, ScriptChapter] | None:
    """Сданная глава, к которой привязан файл: книга — по коду, глава — по той же метке,
    что у `role_deadlines.role_progress`."""
    from app.services.chapter_delivery import _chapter_label

    label = str(audio.chapter or "").strip()
    code = str(audio.book_code or "")
    if not label or not code:
        return None
    for book in db.query(ScriptBook).all():
        # Тем же правилом, что и вся запись: точное равенство кодов не видело бы файл,
        # подписанный той же книгой в другом регистре или транслитом.
        if not book_code_matches_title(str(book.title or ""), code):
            continue
        for chapter in db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id,
                                                      ScriptChapter.released_at.isnot(None)):
            if _chapter_label(chapter) == label:
                return book, chapter
    return None


def notify_takes_in_released(db, items: list[AudioFile], *, send=None) -> bool:
    """Одно сообщение на пачку: какие дубли легли в сданные главы."""
    lines = []
    for audio in items:
        if audio.kind != "take":
            continue
        found = _released_chapter_for(db, audio)
        if found is None:
            continue
        book, _chapter = found
        link = _take_link(audio.id)
        lines.append(f"• {book.title}, {audio.chapter}, роль «{audio.role}», диктор {audio.actor_name}: "
                     f"{audio.canonical_filename}" + (f"\n  {link}" if link else ""))
    if not lines:
        return False
    return _deliver(db, "В сданную главу пришёл дубль:\n" + "\n".join(lines), send)


def notify_recast_in_released(db, character: Character, *, old_actor: str, new_actor: str, send=None) -> bool:
    """Роль сменила актёра, а её реплики есть в сданных главах."""
    from app.services.role_deadlines import role_chapters

    chapters = role_chapters(db, character)
    if not chapters:
        return False
    released = db.query(ScriptChapter).filter(
        ScriptChapter.book_id == character.book_id, ScriptChapter.released_at.isnot(None),
        ScriptChapter.chapter_index.in_(sorted(chapters))).all()
    if not released:
        return False
    book = db.get(ScriptBook, character.book_id)
    labels = ", ".join(chapters[int(ch.chapter_index)] for ch in sorted(released, key=lambda c: c.chapter_index))
    text = (f"Рекаст в сданных главах: роль «{character.name}» («{getattr(book, 'title', '')}») — "
            f"{old_actor or 'без актёра'} → {new_actor or 'без актёра'}. "
            f"Роль говорит в сданных главах: {labels}.")
    return _deliver(db, text, send)


def missing_alert_text(db, chapter_id: str, new_missing: dict[str, list[str]]) -> str:
    """Текст тревоги о новых пропусках в сданной главе; "" — тревожить не о чем.

    Отдельно от отправки затем, чтобы вызывающий, который коммитит сам (фон, скрипт),
    получил текст и отправил его ПОСЛЕ коммита: тревога до упавшего коммита ушла бы
    снова на следующем пересчёте.
    """
    if not new_missing:
        return ""
    chapter = db.get(ScriptChapter, chapter_id)
    if chapter is None or chapter.released_at is None:
        return ""
    book = db.get(ScriptBook, chapter.book_id)
    parts = [f"Новые пропуски в сданной главе «{chapter.chapter_title}» («{getattr(book, 'title', '')}»):"]
    for role, texts in new_missing.items():
        parts.append(f"• {role}: " + " / ".join(texts))
    return "\n".join(parts)


def send_owner_alert(db, text: str, *, send=None) -> bool:
    """Отправить готовый текст тревоги владельцу (см. `missing_alert_text`)."""
    return _deliver(db, text, send) if text else False


def notify_missing_in_released(db, chapter_id: str, new_missing: dict[str, list[str]], *, send=None) -> bool:
    """Пересчёт сверки нашёл новые пропуски в сданной главе."""
    return send_owner_alert(db, missing_alert_text(db, chapter_id, new_missing), send=send)
