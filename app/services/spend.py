"""Журнал трат на нейросети: сколько стоил каждый вызов, сколько ушло за месяц, хватит ли лимита.

Рубли считаются по цене модели в момент вызова. Цена — вписанная студией (`model_prices`)
или встроенная, только с проверяемым источником: выдуманная цена хуже неизвестной, потому
что лимит на неё бы опирался. Неизвестная цена — `rub is None`, а не ноль.
"""
from __future__ import annotations

import json
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
    #: во сколько раз дороже в пиковые часы провайдера (DeepSeek — вдвое, по каталогу);
    #: вписанная студией цена — одна на все часы
    peak_multiplier: float = 1.0


def _builtin() -> dict[tuple[str, str], Price]:
    from app.v2.model_catalog import CATALOG

    prices = {(item.provider, item.model): Price("tokens", item.price_in, item.price_out, None, item.currency,
                                                 float(item.peak_multiplier or 1.0))
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


def to_rub(db, price: Price | None, *, input_units: int, output_units: int, peak: bool = False) -> float | None:
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
    if peak:
        amount *= price.peak_multiplier
    if price.currency == "USD":
        rate = _usd_rate(db)
        if rate <= 0:
            return None
        amount *= rate
    elif price.currency != "RUB":
        return None
    return round(amount, 4)


def record(db, *, step: str, provider: str, model: str, unit: str, input_units: int, output_units: int,
           book_id: str = "", chapter_id: str = "", run_id: str = "", now: datetime | None = None) -> SpendEntry:
    from app.v2.model_catalog import is_peak

    now = now or utcnow_naive()
    price = price_for(db, provider, model)
    if price is not None and price.unit != unit:
        price = None  # цена за минуты к токенам не прикладывается
    rub = to_rub(db, price, input_units=int(input_units or 0), output_units=int(output_units or 0),
                 peak=price is not None and price.peak_multiplier != 1.0 and is_peak(now))
    row = SpendEntry(created_at=now, step=step or "other", provider=provider, model=model, unit=unit,
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
    """Месяц одним проходом базы: суммы и разбивки считает SQL, в память — только 50 последних.
    Месяц разметки — десятки тысяч строк, а сводку зовёт каждый запуск и каждая страница."""
    from sqlalchemy import case, func

    from app.services.studio_settings import studio_settings

    drain_spool()
    start, end = month_bounds(key)
    in_month = (SpendEntry.created_at >= start, SpendEntry.created_at < end)
    unknown = func.sum(case((SpendEntry.rub.is_(None), 1), else_=0))

    def buckets(column) -> dict[str, dict]:
        rows = db.query(column, func.coalesce(func.sum(SpendEntry.rub), 0.0), func.count(SpendEntry.id), unknown) \
            .filter(*in_month).group_by(column).all()
        return {name: {"rub": float(rub or 0), "calls": int(calls), "unknown_calls": int(unk or 0)}
                for name, rub, calls, unk in rows}

    by_step, by_provider = buckets(SpendEntry.step), buckets(SpendEntry.provider)
    total = sum(item["rub"] for item in by_step.values())
    rows = db.query(SpendEntry).filter(*in_month).order_by(SpendEntry.created_at.desc()).limit(50).all()
    # Название книги — на момент показа; удалённая книга остаётся пустым названием, а строка
    # журнала живёт (траты сохраняются и после удаления книги).
    from app.models import ScriptBook

    ids = {r.book_id for r in rows if r.book_id}
    titles = {bid: (display or title or "") for bid, display, title in
              db.query(ScriptBook.id, ScriptBook.display_title, ScriptBook.title).filter(ScriptBook.id.in_(ids)).all()} \
        if ids else {}
    limit = int(studio_settings(db).monthly_limit_rub or 0)
    return {
        "month": key, "total_rub": round(total, 2),
        "unknown_calls": sum(item["unknown_calls"] for item in by_step.values()),
        "limit_rub": limit, "left_rub": max(0.0, round(limit - total, 2)) if limit else None,
        "by_step": by_step, "by_provider": by_provider,
        "recent": [{"created_at": r.created_at.isoformat() + "Z", "step": r.step, "provider": r.provider,
                    "model": r.model, "book_id": r.book_id, "book_title": titles.get(r.book_id, ""), "unit": r.unit, "input_units": r.input_units,
                    "output_units": r.output_units, "rub": r.rub} for r in rows],
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


#: сколько ждать чужую блокировку записи SQLite. Вызывающий часто сам держит транзакцию
#: (разметка, распознавание): ждать его — значит ждать себя, 30 секунд и потерянную строку.
LOCK_WAIT_MS = 2000


@contextmanager
def _short_wait(db):
    if db.get_bind().dialect.name != "sqlite":
        yield
        return
    from sqlalchemy import text

    before = int(db.execute(text("PRAGMA busy_timeout")).scalar() or 0)
    db.execute(text(f"PRAGMA busy_timeout = {int(LOCK_WAIT_MS)}"))
    try:
        yield
    finally:
        try:
            db.rollback()
            db.execute(text(f"PRAGMA busy_timeout = {before}"))
        except Exception:  # noqa: BLE001 — соединение всё равно вернётся в пул и будет закрыто
            logger.debug("spend: не вернул busy_timeout", exc_info=True)


def _spool_path():
    """Файл отложенных строк рядом с базой. Он переживает `os._exit` рабочего процесса RQ,
    а фоновый поток с очередью — нет: строки умерли бы вместе с процессом задачи."""
    from pathlib import Path

    try:
        bind = SessionLocal.kw.get("bind")
        database = bind.url.database if bind.dialect.name == "sqlite" else None
    except Exception:  # noqa: BLE001
        return None
    if not database or database == ":memory:":
        return None
    return Path(f"{database}-spend-spool.jsonl")


def _write(db, fields: dict) -> None:
    created = datetime.fromisoformat(fields["created_at"])
    row = record(db, step=fields["step"], provider=fields["provider"], model=fields["model"], unit=fields["unit"],
                 input_units=fields["input_units"], output_units=fields["output_units"], book_id=fields["book_id"],
                 chapter_id=fields["chapter_id"], run_id=fields["run_id"], now=created)
    if fields.get("estimated"):
        # провайдер не сказал, сколько потратил: оценка токенов — не повод писать рубли
        row.rub, row.price_known = None, False


def drain_spool() -> int:
    """Перенести отложенные строки в базу. Файл чистится только после удачного commit."""
    import fcntl

    path = _spool_path()
    if path is None or not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        with open(path, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            lines = [line for line in fh.read().splitlines() if line.strip()]
            with SessionLocal() as db, _short_wait(db):
                for line in lines:
                    _write(db, json.loads(line))
                db.commit()
            fh.seek(0)
            fh.truncate()
            return len(lines)
    except Exception:  # noqa: BLE001 — не вышло сейчас, выйдет при следующем обращении
        logger.warning("spend: отложенные строки пока не перенесены", exc_info=True)
        return 0


def _spool(fields: dict) -> bool:
    import fcntl

    path = _spool_path()
    if path is None:
        return False
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(fields, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def record_call(provider: str, model: str, *, unit: str, input_units: int, output_units: int = 0,
                step: str | None = None, book_id: str = "", chapter_id: str = "", run_id: str = "",
                estimated: bool = False) -> None:
    """Записать вызов в журнал своей сессией. Сбой записи не роняет работу: база занята —
    строка ложится в файл рядом с базой и переносится при следующем обращении к журналу."""
    ctx = _CONTEXT.get()
    fields = {"created_at": utcnow_naive().strftime("%Y-%m-%dT%H:%M:%S.%f"),  # время вызова, не ответа базы
               "step": step or ctx.get("step") or "other",
              "provider": provider, "model": model, "unit": unit, "input_units": int(input_units or 0),
              "output_units": int(output_units or 0), "book_id": book_id or ctx.get("book_id", ""),
              "chapter_id": chapter_id or ctx.get("chapter_id", ""), "run_id": run_id or ctx.get("run_id", ""),
              "estimated": bool(estimated)}
    drain_spool()
    try:
        with SessionLocal() as db, _short_wait(db):
            _write(db, fields)
            db.commit()
    except Exception:  # noqa: BLE001 — учёт не должен ронять платный вызов
        if _spool(fields):
            logger.warning("spend: база занята, вызов %s/%s отложен в файл", provider, model)
        else:
            logger.exception("spend: не записал вызов %s/%s", provider, model)


def provider_from_url(base_url: str) -> str:
    url = str(base_url or "").lower()
    for marker, name in (("routerai", "routerai"), ("openrouter", "openrouter"), ("deepseek", "deepseek"),
                         ("anthropic", "claude"), ("openai", "openai"), ("z.ai", "zai")):
        if marker in url:
            return name
    return "other"


# --- лимит ----------------------------------------------------------------------------
import threading  # noqa: E402

from app.models import AuditLog, SpendHold  # noqa: E402

#: сколько держится смета запущенного прогона: книга размечается часами, а мёртвый
#: прогон не должен занимать остаток месяца вечно
HOLD_HOURS = 12
#: проверка и резерв — одним шагом: два запроса в одну секунду иначе видят один остаток
_START_LOCK = threading.Lock()


def _held(db, now: datetime) -> float:
    """Ещё не потраченное из смет недавних запусков. Траты после запуска гасят резерв, а не
    считаются второй раз: что прогон уже потратил, сидит в сумме месяца."""
    from sqlalchemy import func

    since = max(now - timedelta(hours=HOLD_HOURS), month_bounds(month_key(now))[0])
    holds = (db.query(SpendHold.created_at, SpendHold.estimate_rub).filter(SpendHold.created_at >= since)
             .order_by(SpendHold.created_at).all())
    if not holds:
        return 0.0
    spent = db.query(func.coalesce(func.sum(SpendEntry.rub), 0.0)).filter(
        SpendEntry.created_at >= holds[0][0], SpendEntry.rub.is_not(None)).scalar()
    return max(0.0, sum(float(e or 0) for _, e in holds) - float(spent or 0))


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
    now = now or utcnow_naive()
    with _START_LOCK:
        summary = month_summary(db, month_key(now))
        limit = int(summary["limit_rub"] or 0)
        estimate = float(estimate_rub or 0)
        if not limit:
            return LimitDecision(True, estimate, None, 0, unknown_price)
        if estimate <= 0 and not unknown_price:
            # бесплатный прогон (разбивка, ударения) лимит не трогает и при исчерпанном месяце
            return LimitDecision(True, 0.0, None, limit, False)
        left = max(0.0, round(float(summary["left_rub"] or 0) - _held(db, now), 2))
        decision = LimitDecision(estimate <= left and left > 0, estimate, left, limit, unknown_price)
        if not decision.allowed and not override:
            return decision
        if not decision.allowed:
            db.add(AuditLog(user_id=actor_uid, entity_type="spend_limit", entity_id=month_key(now),
                            action="override",
                            payload_json=json.dumps({"what": what, **decision.payload()}, ensure_ascii=False)))
        db.add(SpendHold(created_at=now, estimate_rub=estimate, what=str(what or "")[:80]))
        db.commit()  # резерв должен быть виден следующему запросу до того, как он проверит остаток
        return LimitDecision(True, estimate, left, limit, unknown_price)


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
    if not send(text):
        return 0  # бот не ответил — попробуем следующим проходом, а не забудем порог
    row.spend_warned_month = f"{key}:{reached}"
    db.flush()
    return 1


# --- сметы запуска --------------------------------------------------------------------
#: токенов на слово книги (замер прод-данных 07.10: разметка 5,6–8,5 вход / 1,3–2,3 выход,
#: персонажи ≈ 9,7 / 1,2). Смета берёт верх: заниженная смета хуже для бюджета.
TOKENS_PER_WORD = {"attribution": (8.5, 2.4), "characters": (10.0, 1.2)}


#: шаги, которые платят за минуты, а не за токены
SECOND_STEPS = {"asr", "ambient_audio"}


def _priced(db, provider: str, model: str, unit: str) -> bool:
    """Посчитается ли вызов в рублях: цена есть, в нужной единице, и курс для долларов задан."""
    price = price_for(db, provider, model)
    return price is not None and price.unit == unit and \
        to_rub(db, price, input_units=60, output_units=1) is not None


def unknown_for(db, steps) -> bool:
    """Есть ли среди моделей шагов модель, чьи траты не посчитаются в рублях — и не войдут в лимит."""
    from app.services.step_models import step_model

    return any(not _priced(db, *step_model(step), "seconds" if step in SECOND_STEPS else "tokens")
               for step in steps)


def _book_words(db, book_id: str) -> int:
    import re

    from app.models import ScriptChapter
    from app.v2.models import V2Segment

    word = re.compile(r"\w+")
    texts = [t for (t,) in db.query(V2Segment.text).filter(V2Segment.book_id == book_id).all()]
    if not texts:
        texts = [t for (t,) in db.query(ScriptChapter.source_text).filter(ScriptChapter.book_id == book_id).all()]
    return sum(len(word.findall(t or "")) for t in texts)


def estimate_markup(db, book, steps, *, force: bool = False) -> tuple[float, bool]:
    """(рубли, есть ли неизвестная цена) для прогона разметки книги по выбранным шагам.
    Сметы считают по пиковому тарифу: прогон идёт часами и заходит в дорогие окна."""
    from app.models import Character
    from app.services.step_models import step_model
    from app.v2 import model_catalog

    words = _book_words(db, book.id)
    total, unknown = 0.0, False
    parts = []
    if "attribute" in steps:
        chosen = model_catalog.for_book(book)
        parts.append(("attribution", chosen.provider, chosen.model))
    # с force движок ищет персонажей заново, даже если они уже есть
    if "cast" in steps and (force or not db.query(Character.id).filter(Character.book_id == book.id).first()):
        parts.append(("characters", *step_model("characters")))
    for step, provider, model in parts:
        per_in, per_out = TOKENS_PER_WORD[step]
        rub = to_rub(db, price_for(db, provider, model), input_units=int(words * per_in),
                     output_units=int(words * per_out), peak=True)
        if rub is None:
            unknown = True
        else:
            total += rub
    return round(total, 2), unknown


#: токенов на описание одной сцены: отрывок 1500 знаков, правила и ответ — с запасом вверх
AMBIENT_TEXT_TOKENS = (4000, 1000)


def estimate_ambient(db, items) -> tuple[float, bool]:
    """Музыка — секунды сцен × цена звука; плюс описание каждой сцены, у которой нет готового
    промпта (его пишет текстовая модель перед треком)."""
    from app.services.step_models import step_model

    seconds = sum(int(item.get("seconds") or 0) for item in items)
    price = price_for(db, *step_model("ambient_audio"))
    music = to_rub(db, price if price is not None and price.unit == "seconds" else None,
                   input_units=seconds, output_units=0)
    total, unknown = (music or 0.0), music is None
    texts = sum(1 for item in items if not str(item.get("prompt") or "").strip())
    if texts:
        text_price = price_for(db, *step_model("ambient_text"))
        words = to_rub(db, text_price if text_price is not None and text_price.unit == "tokens" else None,
                       input_units=texts * AMBIENT_TEXT_TOKENS[0], output_units=texts * AMBIENT_TEXT_TOKENS[1],
                       peak=True)
        total, unknown = total + (words or 0.0), unknown or words is None
    return round(total, 2), unknown


def price_view(db, provider: str, model: str) -> dict:
    """Цена модели для страницы: откуда она — вписана студией, встроенная или неизвестна."""
    row = db.get(ModelPrice, (provider, model))
    price = price_for(db, provider, model)
    source = "studio" if row is not None else ("builtin" if price is not None else "none")
    if price is None:
        return {"source": "none", "unit": "", "price_in": None, "price_out": None, "price_unit": None, "currency": ""}
    return {"source": source, "unit": price.unit, "price_in": price.price_in, "price_out": price.price_out,
            "price_unit": price.price_unit, "currency": price.currency}


def months(db) -> list[str]:
    """Месяцы, за которые есть траты, свежие первыми; текущий — всегда."""
    from sqlalchemy import func

    first = db.query(func.min(SpendEntry.created_at)).scalar()
    current = month_key(utcnow_naive())
    if first is None:
        return [current]
    out, key = [], current
    stop = month_key(first)
    while True:
        out.append(key)
        if key <= stop or len(out) >= 120:
            break
        year, month = (int(p) for p in key.split("-"))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
        key = f"{year:04d}-{month:02d}"
    return out
