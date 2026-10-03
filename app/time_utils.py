from __future__ import annotations

from datetime import UTC, datetime


def utcnow_naive() -> datetime:
    """Return UTC timestamp as naive datetime for legacy DB compatibility."""
    return datetime.now(UTC).replace(tzinfo=None)


def iso_utc(value: datetime | None) -> str | None:
    """Время из базы — наружу, в ISO с явным `+00:00`. `None` — если времени нет.

    В базе время лежит наивным UTC (`utcnow_naive`). Голый `.isoformat()` отдавал его без
    пояса, а браузер читает такую строку как МЕСТНОЕ время: у диктора в Москве «загружено
    в 15:00» показывалось вместо 18:00. Миллисекунды, а не микросекунды: так строку
    одинаково разбирают все браузеры.
    """
    if value is None:
        return None
    moment = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return moment.isoformat(timespec="milliseconds")
