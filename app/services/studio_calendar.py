"""Один календарь установки; пустой профиль сохраняет прежние правила UTC+3."""
from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@lru_cache(maxsize=128)
def calendar_zone(name: str):
    if not name:
        return timezone(timedelta(hours=3), "MSK")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid studio IANA timezone: {name!r}") from exc


def studio_timezone(db) -> str:
    from app.models import StudioCalendarProfile

    row = db.info.get("studio_calendar_profile")
    if row is None:
        row = db.get(StudioCalendarProfile, "default")
        if row is not None:
            db.info["studio_calendar_profile"] = row
    name = str(row.timezone or "") if row is not None else ""
    calendar_zone(name)
    return name


def local_time(moment: datetime, name: str = "") -> datetime:
    utc = moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    return utc.astimezone(calendar_zone(name))


def midnight_utc(day: date, name: str = "") -> datetime:
    local = datetime.combine(day, time.min, calendar_zone(name))
    utc = local.astimezone(UTC)
    # Некоторые зоны переносили полночь или пропускали целый день. Присвоение
    # tzinfo само по себе это не проверяет; неверный срок нельзя сохранять молча.
    if utc.astimezone(calendar_zone(name)).replace(tzinfo=None) != local.replace(tzinfo=None):
        raise ValueError(f"Local midnight does not exist: {day} in {name}")
    return utc.replace(tzinfo=None)


def end_of_day(day: date, name: str = "") -> datetime:
    midnight_utc(day, name)
    return midnight_utc(day + timedelta(days=1), name) - timedelta(seconds=1)


def timezone_label(name: str, moment: datetime) -> str:
    return f"{name} ({local_time(moment, name):%Z})" if name else "MSK"


def calendar_profile(db) -> dict:
    name = studio_timezone(db)
    return {"mode": "iana" if name else "legacy", "timezone": name or None,
            "display_timezone": name or "Asia/Yekaterinburg",
            "deadline_timezone": name or "Europe/Moscow",
            "spending_timezone": name or "Etc/GMT-3"}
