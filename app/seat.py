"""Режим «одно место»: настольная программа одного диктора-режиссёра.

Одна настройка вместо развилки по коду: VPS-режим (`studio`) не меняется и проверяется тем
же сьютом. Режим читается в момент вызова — тесты и точка входа переключают его после импорта.
"""
import hashlib
import re

from app.config import settings
from app.time_utils import utcnow_naive

OWNER_ID = "local-owner"
ALLOWED_HOSTS = ("127.0.0.1", "localhost")
INTERRUPTED = "Приложение закрыли во время прогона — запустите его заново."


def one_seat() -> bool:
    return str(settings.seat_mode or "").strip().lower() == "one"


#: многопользовательские части, которых в «одном месте» нет: учётки, Telegram, вход и выход,
#: пробы и голоса автора, сроки ролей, старая загрузка дубля дикторами. «Дикторы» остаются —
#: на них стоит выбор актёра в касте. Список — выгрузка маршрутов 08.10.
HIDDEN_PREFIXES: tuple[str, ...] = (
    "/api/users", "/api/telegram", "/auth/telegram", "/api/me/bot-reach", "/login", "/logout",
    "/api/auditions", "/api/v2/auditions", "/api/deadlines", "/dictor-pro",
)
HIDDEN_PATTERNS = (re.compile(r"^/api/v2/books/[^/]+/auditions$"),)


def is_hidden(path: str) -> bool:
    return (any(path == p or path.startswith(p + "/") for p in HIDDEN_PREFIXES)
            or any(p.match(path) for p in HIDDEN_PATTERNS))


def seat_fingerprint() -> str:
    """Отпечаток ключа запуска — в подписанной сессии. Новый запуск — новый ключ, и cookie
    прошлого запуска перестаёт действовать без всякого списка отозванных."""
    return hashlib.sha256(str(settings.seat_token or "").encode()).hexdigest()[:32]


def local_owner(db):
    """Учётка владельца программы: одна, создаётся при первом входе. Нужна там, где код
    пишет автора действия (журнал, голоса, траты)."""
    from app.models import User, UserRole

    user = db.get(User, OWNER_ID)
    if user is None:
        user = User(id=OWNER_ID, login="owner", password_hash="", display_name="Владелец", is_active="true")
        db.add(user)
        db.add(UserRole(user_id=OWNER_ID, role="admin"))
        db.add(UserRole(user_id=OWNER_ID, role="author"))
        db.commit()
    return user


def mark_interrupted_runs(db) -> int:
    """Прогоны, оставшиеся «идущими» от прошлого запуска: поток, который их вёл, умер вместе
    с программой. Без отметки кнопки запуска висели бы занятыми, пока не истечёт час без
    сердцебиения."""
    from app.models import BackgroundRun, ScriptBook
    from app.v2.models import V2Run

    now = utcnow_naive()
    count = 0
    for run in db.query(BackgroundRun).filter(BackgroundRun.status.in_(("queued", "running"))):
        run.status, run.error_message, run.finished_at = "failed", INTERRUPTED, now
        count += 1
    for run in db.query(V2Run).filter(V2Run.status.in_(("queued", "running"))):
        run.status, run.error, run.finished_at, run.updated_at = "failed", INTERRUPTED, now, now
        book = db.get(ScriptBook, run.book_id)
        # «processing» без живого прогона держал бы кнопки хаба; «stopped» — как обычная остановка
        if book is not None and book.status == "processing":
            book.status = "stopped"
        count += 1
    db.commit()
    return count
