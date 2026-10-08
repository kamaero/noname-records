"""Режим «одно место»: настольная программа одного диктора-режиссёра.

Одна настройка вместо развилки по коду: VPS-режим (`studio`) не меняется и проверяется тем
же сьютом. Режим читается в момент вызова — тесты и точка входа переключают его после импорта.
"""
from app.config import settings
from app.time_utils import utcnow_naive

INTERRUPTED = "Приложение закрыли во время прогона — запустите его заново."


def one_seat() -> bool:
    return str(settings.seat_mode or "").strip().lower() == "one"


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
