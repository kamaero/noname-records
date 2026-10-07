"""Журнал трат на нейросети: сколько стоил каждый вызов, сколько ушло за месяц, хватит ли лимита.

Рубли считаются по цене модели в момент вызова. Цена — вписанная студией (`model_prices`)
или встроенная, только с проверяемым источником: выдуманная цена хуже неизвестной, потому
что лимит на неё бы опирался. Неизвестная цена — `rub is None`, а не ноль.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models import ModelPrice, SpendEntry


@dataclass(frozen=True)
class Price:
    unit: str                  # tokens | seconds
    price_in: float | None     # за 1 млн входных токенов
    price_out: float | None    # за 1 млн выходных токенов
    price_unit: float | None   # за минуту (unit == seconds)
    currency: str              # RUB | USD


def _builtin() -> dict[tuple[str, str], Price]:
    from app.v2.model_catalog import CATALOG

    prices = {(item.provider, item.model): Price("tokens", item.price_in, item.price_out, None, item.currency)
              for item in CATALOG}
    # https://openai.com/api/pricing — Whisper: $0.006 за минуту
    prices[("openai", "whisper-1")] = Price("seconds", None, None, 0.006, "USD")
    return prices


BUILTIN_PRICES = _builtin()
MSK = timedelta(hours=3)


def price_for(db, provider: str, model: str) -> Price | None:
    row = db.get(ModelPrice, (provider, model))
    if row is not None:
        return Price(row.unit, row.price_in, row.price_out, row.price_unit, row.currency)
    return BUILTIN_PRICES.get((provider, model))


def _usd_rate(db) -> float:
    from app.services.studio_settings import studio_settings

    # Сохранённый курс как есть: 0 значит «не знаем», а не подставленный запасной курс.
    return float(studio_settings(db).usd_rub_rate or 0.0)


def to_rub(db, price: Price | None, *, input_units: int, output_units: int) -> float | None:
    if price is None:
        return None
    if price.unit == "seconds":
        if price.price_unit is None:
            return None
        amount = input_units / 60 * price.price_unit
    else:
        if price.price_in is None or price.price_out is None:
            return None
        amount = input_units / 1_000_000 * price.price_in + output_units / 1_000_000 * price.price_out
    if price.currency == "USD":
        rate = _usd_rate(db)
        if rate <= 0:
            return None
        amount *= rate
    elif price.currency != "RUB":
        return None
    return round(amount, 4)


def record(db, *, step: str, provider: str, model: str, unit: str, input_units: int, output_units: int,
           book_id: str = "", chapter_id: str = "", run_id: str = "") -> SpendEntry:
    price = price_for(db, provider, model)
    if price is not None and price.unit != unit:
        price = None  # цена за минуты к токенам не прикладывается
    rub = to_rub(db, price, input_units=int(input_units or 0), output_units=int(output_units or 0))
    row = SpendEntry(step=step or "other", provider=provider, model=model, unit=unit,
                     input_units=int(input_units or 0), output_units=int(output_units or 0),
                     book_id=book_id or "", chapter_id=chapter_id or "", run_id=run_id or "",
                     rub=rub, price_known=rub is not None)
    db.add(row)
    db.flush()
    return row


def month_key(now: datetime) -> str:
    """Календарный месяц по Москве: 23:30 МСК 31-го и 00:10 МСК 1-го — разные месяцы."""
    return (now + MSK).strftime("%Y-%m")


def month_bounds(key: str) -> tuple[datetime, datetime]:
    year, month = (int(part) for part in key.split("-"))
    start = datetime(year, month, 1) - MSK
    end = (datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)) - MSK
    return start, end


def month_summary(db, key: str) -> dict:
    from app.services.studio_settings import studio_settings

    start, end = month_bounds(key)
    rows = (db.query(SpendEntry).filter(SpendEntry.created_at >= start, SpendEntry.created_at < end)
            .order_by(SpendEntry.created_at.desc()).all())
    total = sum(r.rub for r in rows if r.rub is not None)
    by_step: dict[str, dict] = {}
    by_provider: dict[str, dict] = {}
    for r in rows:
        for bucket, name in ((by_step, r.step), (by_provider, r.provider)):
            item = bucket.setdefault(name, {"rub": 0.0, "calls": 0, "unknown_calls": 0})
            item["calls"] += 1
            if r.rub is None:
                item["unknown_calls"] += 1
            else:
                item["rub"] += r.rub
    limit = int(studio_settings(db).monthly_limit_rub or 0)
    return {
        "month": key, "total_rub": round(total, 2), "unknown_calls": sum(1 for r in rows if r.rub is None),
        "limit_rub": limit, "left_rub": max(0.0, round(limit - total, 2)) if limit else None,
        "by_step": by_step, "by_provider": by_provider,
        "recent": [{"created_at": r.created_at.isoformat() + "Z", "step": r.step, "provider": r.provider,
                    "model": r.model, "book_id": r.book_id, "unit": r.unit, "input_units": r.input_units,
                    "output_units": r.output_units, "rub": r.rub} for r in rows[:50]],
    }
