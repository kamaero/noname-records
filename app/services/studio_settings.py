"""Studio-wide defaults, read and written through one row.

Two settings: the rate a role is priced at when nobody set a rate for it, and how many
rubles a dollar is. Both used to be constants — the first in `cast_budget` and a second
copy in the cast table's front-end, the second nowhere at all, because model prices were
kept in rubles that would quietly rot.
"""
from __future__ import annotations

from app.models import ScriptBook, StudioSettings

ROW_ID = "default"
FALLBACK_RATE_RUB_PER_MIN = 1000
#: Central bank rate on 2026-09-05, the day the DeepSeek prices were entered.
FALLBACK_USD_RUB_RATE = 86.59


def studio_settings(db) -> StudioSettings:
    """The single row, created on first read so a fresh database needs no seed."""
    row = db.get(StudioSettings, ROW_ID)
    if row is None:
        row = StudioSettings(id=ROW_ID, default_rate_rub_per_min=FALLBACK_RATE_RUB_PER_MIN,
                             usd_rub_rate=FALLBACK_USD_RUB_RATE)
        db.add(row)
        db.flush()
    return row


def default_rate_rub_per_min(db) -> int:
    """Never zero: a zero rate would silently price a whole book at nothing."""
    rate = int(studio_settings(db).default_rate_rub_per_min or 0)
    return rate if rate > 0 else FALLBACK_RATE_RUB_PER_MIN


def usd_rub_rate(db) -> float:
    """Rubles per dollar. Falls back to the rate of the day the prices were entered."""
    rate = float(studio_settings(db).usd_rub_rate or 0.0)
    return rate if rate > 0 else FALLBACK_USD_RUB_RATE


def set_usd_rub_rate(db, rate: float, *, updated_by: str = "") -> float:
    row = studio_settings(db)
    row.usd_rub_rate = max(0.0, float(rate or 0.0))
    row.updated_by = str(updated_by or "")[:120]
    return float(row.usd_rub_rate)


def set_default_rate(db, rate: int, *, updated_by: str = "") -> int:
    row = studio_settings(db)
    row.default_rate_rub_per_min = max(1, int(rate or 0))
    row.updated_by = str(updated_by or "")[:120]
    return int(row.default_rate_rub_per_min)


def active_book(db) -> ScriptBook | None:
    """Книга, которую студия записывает сейчас. Удалённая книга читается как «никакой»:
    бот не должен принимать дубли в пустоту."""
    book_id = str(studio_settings(db).recording_book_id or "")
    book = db.get(ScriptBook, book_id) if book_id else None
    # Озвученная книга записи не принимает: бот не должен брать дубли в законченную книгу,
    # даже если `recording_book_id` на неё ещё указывает (voice_book его сбрасывает, но
    # ручная правка или гонка могли оставить). Поле читаем прямо, без book_lifecycle:
    # тот сам импортирует этот модуль.
    if book is not None and getattr(book, "voiced_at", None) is not None:
        return None
    return book


def set_active_book(db, book_id: str | None, *, updated_by: str) -> None:
    """Одна книга или никакой. Поле одно на студию, так что включение второй книги
    снимает первую само, без отдельного сброса."""
    row = studio_settings(db)
    if book_id:
        if db.get(ScriptBook, book_id) is None:
            raise ValueError("book_not_found")
        row.recording_book_id = book_id
    else:
        row.recording_book_id = ""
    row.updated_by = str(updated_by or "")[:120]
