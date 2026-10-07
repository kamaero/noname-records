"""Журнал трат на нейросети: сколько стоил каждый вызов, сколько ушло за месяц, хватит ли лимита.

Рубли считаются по цене модели в момент вызова. Цена — вписанная студией (`model_prices`)
или встроенная, только с проверяемым источником: выдуманная цена хуже неизвестной, потому
что лимит на неё бы опирался. Неизвестная цена — `rub is None`, а не ноль.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models import ModelPrice, SpendEntry
from app.time_utils import utcnow_naive


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


# --- запись из прогонов ---------------------------------------------------------------
import contextvars  # noqa: E402
import logging  # noqa: E402
from contextlib import contextmanager  # noqa: E402

from app.db import SessionLocal  # noqa: E402

logger = logging.getLogger(__name__)

#: какой шаг, книга, глава и прогон сейчас тратят деньги; ставит движок на время прогона.
#: Пул потоков контекст не наследует — рабочая функция в потоке ставит его сама.
_CONTEXT: contextvars.ContextVar[dict] = contextvars.ContextVar("spend_context", default={})


@contextmanager
def context(step: str, *, book_id: str = "", chapter_id: str = "", run_id: str = ""):
    token = _CONTEXT.set({"step": step, "book_id": book_id or "", "chapter_id": chapter_id or "",
                          "run_id": run_id or ""})
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def record_call(provider: str, model: str, *, unit: str, input_units: int, output_units: int = 0,
                step: str | None = None, book_id: str = "", chapter_id: str = "", run_id: str = "",
                estimated: bool = False) -> None:
    """Записать вызов в журнал своей сессией. Сбой записи не роняет работу — только лог:
    распознанный дубль или размеченная глава дороже строчки учёта."""
    ctx = _CONTEXT.get()
    try:
        with SessionLocal() as db:
            row = record(db, step=step or ctx.get("step") or "other", provider=provider, model=model, unit=unit,
                         input_units=input_units, output_units=output_units,
                         book_id=book_id or ctx.get("book_id", ""), chapter_id=chapter_id or ctx.get("chapter_id", ""),
                         run_id=run_id or ctx.get("run_id", ""))
            if estimated:
                # провайдер не сказал, сколько потратил: оценка токенов — не повод писать рубли
                row.rub, row.price_known = None, False
            db.commit()
    except Exception:  # noqa: BLE001 — учёт не должен ронять платный вызов
        logger.exception("spend: не записал вызов %s/%s", provider, model)


def provider_from_url(base_url: str) -> str:
    url = str(base_url or "").lower()
    for marker, name in (("routerai", "routerai"), ("openrouter", "openrouter"), ("deepseek", "deepseek"),
                         ("anthropic", "claude"), ("openai", "openai"), ("z.ai", "zai")):
        if marker in url:
            return name
    return "other"


# --- лимит ----------------------------------------------------------------------------
import json  # noqa: E402

from app.models import AuditLog  # noqa: E402


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    estimate_rub: float
    left_rub: float | None
    limit_rub: int
    unknown_price: bool

    def payload(self) -> dict:
        return {"error": "over_limit", "estimate_rub": round(self.estimate_rub), "left_rub": round(self.left_rub or 0),
                "limit_rub": self.limit_rub}


def check_start(db, *, estimate_rub: float, unknown_price: bool, override: bool, is_admin: bool,
                actor_uid: str, what: str, now: datetime | None = None) -> LimitDecision:
    """Можно ли начать платный прогон. Начатые лимитом не останавливаются (решение владельца):
    сверяется только старт. «Сверх лимита» — только администратор и всегда в журнал."""
    if override and not is_admin:
        raise PermissionError("сверх лимита запускает только администратор")
    summary = month_summary(db, month_key(now or utcnow_naive()))
    limit = int(summary["limit_rub"] or 0)
    estimate = float(estimate_rub or 0)
    if not limit:
        return LimitDecision(True, estimate, None, 0, unknown_price)
    left = float(summary["left_rub"] or 0)
    if estimate <= left and left > 0:
        return LimitDecision(True, estimate, left, limit, unknown_price)
    decision = LimitDecision(False, estimate, left, limit, unknown_price)
    if override:
        db.add(AuditLog(user_id=actor_uid, entity_type="spend_limit", entity_id=month_key(now or utcnow_naive()),
                        action="override", payload_json=json.dumps({"what": what, **decision.payload()}, ensure_ascii=False)))
        db.flush()
        return LimitDecision(True, estimate, left, limit, unknown_price)
    return decision


def warn_thresholds(db, now: datetime, send) -> int:
    """Письмо владельцу при переходе месяца через 80 % и 100 % лимита — по одному на порог.
    Отметка «до какого порога уже написали» хранится в настройках студии, поэтому повторный
    проход в ту же минуту и рестарт воркера второго письма не дают."""
    from app.services.studio_settings import studio_settings

    row = studio_settings(db)
    key = month_key(now)
    summary = month_summary(db, key)
    limit = int(summary["limit_rub"] or 0)
    if not limit:
        return 0
    share = summary["total_rub"] / limit
    reached = 100 if share >= 1 else 80 if share >= 0.8 else 0
    month, _, done = str(row.spend_warned_month or "").partition(":")
    already = int(done or 0) if month == key else 0
    if reached <= already:
        return 0
    text = (f"Траты на нейросети за месяц — {summary['total_rub']:,.0f} ₽ из {limit:,} ₽ ({reached} % лимита)."
            .replace(",", " "))
    if reached >= 100:
        text += " Новые платные прогоны не запустятся без «сверх лимита»."
    send(text)
    row.spend_warned_month = f"{key}:{reached}"
    db.flush()
    return 1


# --- сметы запуска --------------------------------------------------------------------
#: токенов на слово книги (замер прод-данных 07.10: разметка 5,6–8,5 вход / 1,3–2,3 выход,
#: персонажи ≈ 9,7 / 1,2). Смета берёт верх: заниженная смета хуже для бюджета.
TOKENS_PER_WORD = {"attribution": (8.5, 2.4), "characters": (10.0, 1.2)}


def unknown_for(db, steps) -> bool:
    """Есть ли среди моделей шагов модель без цены — тогда её траты не войдут в лимит."""
    from app.services.step_models import step_model

    return any(price_for(db, *step_model(step)) is None for step in steps)


def _book_words(db, book_id: str) -> int:
    import re

    from app.models import ScriptChapter
    from app.v2.models import V2Segment

    word = re.compile(r"\w+")
    texts = [t for (t,) in db.query(V2Segment.text).filter(V2Segment.book_id == book_id).all()]
    if not texts:
        texts = [t for (t,) in db.query(ScriptChapter.source_text).filter(ScriptChapter.book_id == book_id).all()]
    return sum(len(word.findall(t or "")) for t in texts)


def estimate_markup(db, book, steps) -> tuple[float, bool]:
    """(рубли, есть ли неизвестная цена) для прогона разметки книги по выбранным шагам."""
    from app.models import Character
    from app.services.step_models import step_model
    from app.v2 import model_catalog

    words = _book_words(db, book.id)
    total, unknown = 0.0, False
    parts = []
    if "attribute" in steps:
        chosen = model_catalog.for_book(book)
        parts.append(("attribution", chosen.provider, chosen.model))
    if "cast" in steps and not db.query(Character.id).filter(Character.book_id == book.id).first():
        parts.append(("characters", *step_model("characters")))
    for step, provider, model in parts:
        per_in, per_out = TOKENS_PER_WORD[step]
        rub = to_rub(db, price_for(db, provider, model), input_units=int(words * per_in),
                     output_units=int(words * per_out))
        if rub is None:
            unknown = True
        else:
            total += rub
    return round(total, 2), unknown


def estimate_ambient(db, items) -> tuple[float, bool]:
    """Музыка — секунды сцен × цена звука; текст сцены без надёжной сметы — помечается."""
    from app.services.step_models import step_model

    seconds = sum(int(item.get("seconds") or 0) for item in items)
    rub = to_rub(db, price_for(db, *step_model("ambient_audio")), input_units=seconds, output_units=0)
    return (round(rub, 2) if rub is not None else 0.0), rub is None or unknown_for(db, ["ambient_text"])
