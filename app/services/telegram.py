import logging
import re

import requests

from app.config import settings
from app.constants import DICTOR_ROLES
from app.models import Character, ScriptLog, TelegramAuthAccount
from app.services import studio

logger = logging.getLogger(__name__)

#: Пусто, и это намеренно. Здесь полгода стоял чат автора: 6 марта 2026 (`d4ac563`,
#: «suppress telegram notifications for author during debug») его заглушили на время
#: отладки поглавных уведомлений и снять забыли. Заглушка глушила не спам, а ЛИЧНЫЕ
#: письма: «вы утверждены на роль» и замечания по его собственным дублям — автор книги
#: играет в ней роль. От спама защищает другой, штатный механизм: `OWNER_TELEGRAM_ID`
#: заворачивает все уведомления о ходе работ владельцу, и до студии они не доходят вовсе.
#: Список оставлен пустым, а не удалён: если кого-то однажды придётся заглушить
#: по-настоящему, пусть это будет осознанная запись сюда, а не новая ветка в отправке.
NOTIFY_SUPPRESSED_CHAT_IDS: set[str] = set()


def telegram_target_chat_ids(db) -> list[str]:
    configured = [x.strip() for x in (settings.telegram_notify_chat_ids or "").split(",") if x.strip()]
    if configured:
        return configured
    rows = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.is_active == "true").all()
    return [str(row.telegram_user_id) for row in rows if str(row.telegram_user_id).strip()]


def _normalize_person_name(value: str) -> str:
    normalized = (value or "").strip().lower().replace("ё", "е")
    normalized = re.sub(r"[^a-zа-я0-9 ]+", " ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _book_status_message(book, curr: str, detail: str) -> str:
    title = str(getattr(book, "title", "") or getattr(book, "display_title", "") or "Без названия").strip()
    if curr == "failed":
        text = (
            f"❌ {studio.name()} — ошибка\n"
            f"Книга: {title}\n"
            f"Пайплайн остановился с ошибкой. Открой «Подготовка книги», проверь лог и перезапусти после правки."
        )
        if detail:
            text += f"\nПричина: {detail[:220]}"
        return text
    # Success completion (author_review): the pipeline finished, ready to review.
    return (
        f"✅ {studio.name()} — книга обработана\n"
        f"Книга: {title}\n"
        f"Пайплайн завершён, можно проверять разметку во вкладке «Валидация»."
    )


def names_match(actor_name: str, account_name: str) -> bool:
    actor = _normalize_person_name(actor_name)
    account = _normalize_person_name(account_name)
    if not actor or not account:
        return False
    if actor == account:
        return True
    actor_tokens = [item for item in actor.split(" ") if item]
    account_tokens = [item for item in account.split(" ") if item]
    if not actor_tokens or not account_tokens:
        return False
    if len(actor_tokens) == 1:
        return actor_tokens[0] in account_tokens
    overlap = len(set(actor_tokens).intersection(set(account_tokens)))
    return overlap >= min(2, len(actor_tokens))


def resolve_cast_chat_ids(db, book_id: str) -> list[str]:
    """Телеграмы утверждённой касты книги — тех, кого зовут к микрофонам.

    Предложенный агентом («Натали Ким?») не утверждён и рассылки не получает:
    `names_match` знака вопроса не видит, и без `approved_actor` предварительное имя
    доехало бы до человека настоящим письмом «книга готова, занимайте места».
    """
    # Импорт внутри функции: `app.v2.cast_ops` берёт отсюда `names_match`, и на уровне
    # модуля это был бы круг.
    from app.v2.cast_ops import approved_actor

    actor_name_rows = (
        db.query(Character.actor_name)
        .filter(Character.book_id == book_id, Character.actor_name != "")
        .distinct()
        .all()
    )
    actor_names = [name for name in (approved_actor(str(row[0] or "")) for row in actor_name_rows) if name]
    if not actor_names:
        return []
    accounts = db.query(TelegramAuthAccount).filter(
        TelegramAuthAccount.is_active == "true",
        # `dictor` плюс те две роли, что студия свела в неё: список стоял на одних старых
        TelegramAuthAccount.role.in_(sorted(DICTOR_ROLES)),
    ).all()
    out: list[str] = []
    for account in accounts:
        display_name = (account.display_name or "").strip()
        if any(names_match(actor_name, display_name) for actor_name in actor_names):
            out.append(str(account.telegram_user_id))
    return sorted(set(out))


def send_telegram_message(
    db,
    text: str,
    chat_ids: list[str] | None = None,
    disable_web_page_preview: bool = True,
    direct: bool = False,
) -> int:
    """Отправка. `direct` — письмо адресату, а не уведомление о ходе работ.

    `OWNER_TELEGRAM_ID` нарочно подменяет адресата владельцем: уведомления о ходе работ
    идут ему и не разлетаются по студии. Личное «ты утверждён на роль» этой подмене не
    подлежит — иначе оно уедет владельцу, а диктор ничего не узнает.
    """
    if not settings.telegram_notify_enabled:
        return 0
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return 0
    owner_chat_id = "" if direct else str(settings.owner_telegram_id or "").strip()
    if direct:
        resolved_chat_ids = [x.strip() for x in (chat_ids or []) if x.strip()]
    elif owner_chat_id:
        resolved_chat_ids = [owner_chat_id]
    else:
        resolved_chat_ids = [x.strip() for x in (chat_ids or []) if x.strip()]
        if not resolved_chat_ids:
            if db is None:
                return 0
            resolved_chat_ids = telegram_target_chat_ids(db)
    resolved_chat_ids = [chat_id for chat_id in resolved_chat_ids if chat_id not in NOTIFY_SUPPRESSED_CHAT_IDS]
    if not resolved_chat_ids:
        return 0

    sent = 0
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chat_id in resolved_chat_ids:
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": disable_web_page_preview},
                timeout=8,
            )
            if resp.status_code < 400:
                sent += 1
            else:
                logger.warning("Telegram отказал в отправке в чат %s: HTTP %s", chat_id, resp.status_code)
        except Exception as exc:  # noqa: BLE001 — один чат не должен сорвать остальные
            # Только тип: текст ошибки requests содержит адрес запроса, а в нём — токен бота.
            logger.warning("Сообщение в чат %s не ушло: %s", chat_id, type(exc).__name__)
            continue
    return sent


def call_bot_api(method: str, payload: dict) -> bool:
    """Один вызов Bot API от имени бота — ответ человеку, который сам написал.

    Адресат берётся из `payload` как есть: подмены на `OWNER_TELEGRAM_ID` здесь нет и быть
    не должно — иначе пароль диктора ушёл бы владельцу.
    """
    if not settings.telegram_notify_enabled:
        return False
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return False
    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=payload, timeout=8)
    except Exception as exc:  # noqa: BLE001
        # Только тип: текст ошибки requests содержит адрес запроса, а в нём — токен бота.
        logger.warning("Bot API %s не ответил: %s", method, type(exc).__name__)
        return False
    if resp.status_code >= 400:
        logger.warning("Bot API %s отказал: HTTP %s", method, resp.status_code)
        return False
    return True


# Terminal outcomes worth one notification each. `stalled`/`processing`/etc. are intentionally
# excluded — those transient states were the source of recovery-driven spam.
_NOTIFY_STATUSES = {"failed", "author_review"}


def notify_book_status_change(db, book, previous_status: str, new_status: str, detail: str = "") -> int:
    prev = str(previous_status or "").strip()
    curr = str(new_status or "").strip()
    if not book or curr not in _NOTIFY_STATUSES:
        return 0
    # Dedup: exactly one message per outcome. `last_notified_status` is re-armed ("") when a new
    # pipeline run starts (ensure_pipeline_run), so each run can emit one notification, while
    # recovery oscillation (e.g. failed↔processing↔failed) never re-pings the same outcome.
    if str(getattr(book, "last_notified_status", "") or "") == curr:
        return 0
    text = _book_status_message(book, curr, detail)
    sent = send_telegram_message(db, text)
    book.last_notified_status = curr
    db.add(
        ScriptLog(
            book_id=book.id,
            level="info",
            message=f"TG_NOTIFY_BOOK_STATUS:{prev or 'none'}->{curr}: sent={sent};detail={detail[:120]}",
        )
    )
    return sent


def notify_book_ready_for_recording(db, book) -> tuple[int, int]:
    marker = db.query(ScriptLog).filter(
        ScriptLog.book_id == book.id,
        ScriptLog.message.like("TG_NOTIFY_CAST_READY:%"),
    ).first()
    if marker:
        return 0, 0
    cast_chat_ids = resolve_cast_chat_ids(db, book.id)
    # A book is published chapter by chapter now: the author approves as he reads, so
    # «все главы опубликованы» was true only for the last of them. Count instead.
    from app.models import ScriptChapter

    total = db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id).count()
    opened = db.query(ScriptChapter).filter(
        ScriptChapter.book_id == book.id, ScriptChapter.status == "published",
    ).count()
    if opened and opened < total:
        scope = (
            f"Открыто глав: {opened} из {total} — автор проверяет книгу дальше,\n"
            "остальные будут появляться по мере проверки."
        )
    else:
        scope = "Авторская проверка завершена, все главы опубликованы."
    text = (
        f"ЭВМ им. Н. Кадышевой информирует: книга \"{book.title}\" готова к записи.\n"
        "Статус: опубликовано для Dictor Pro/Neo\n"
        f"{scope}\n"
        "Всем занять места у микрофонов!"
    )
    sent = send_telegram_message(db, text, chat_ids=cast_chat_ids or None)
    db.add(
        ScriptLog(
            book_id=book.id,
            level="info",
            message=f"TG_NOTIFY_CAST_READY: sent={sent};cast_targets={len(cast_chat_ids)}",
        )
    )
    return sent, len(cast_chat_ids)
