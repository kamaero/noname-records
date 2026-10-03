"""Приветственное окно диктора: когда показывать и умеет ли бот до него достучаться.

Показ решает чистая функция без побочных эффектов — она не трогает БД и не ходит в
сеть, поэтому все её ветки проверяются без единого мока. Достижимость бота дороже:
это поход в Telegram (`check_bot_reach`), а `/api/me` дёргают на каждый переход по
студии — значит результат живёт в памяти процесса 5 минут на пользователя, а не
спрашивается у Telegram заново на каждый запрос.

И даже промах кэша `/api/me` не ждёт: отдаёт последний известный ответ (или `None`) и
перепроверяет в фоне — не больше одной перепроверки на человека за раз. Ждёт Telegram
только кнопка «Проверить ещё раз» (`fresh=True`): там человек сам попросил ответ.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta

from app.config import settings
from app.services.bot_reach import check_bot_reach

#: сколько держим последний ответ Telegram, прежде чем спросить заново
CACHE_TTL_SECONDS = 5 * 60
#: `/api/me` не должен ждать Telegram дольше этого
REACH_TIMEOUT_SECONDS = 3
#: не дозвонившись через Telegram-вход, ждём сутки, прежде чем напомнить снова
STALE_AFTER = timedelta(hours=24)

logger = logging.getLogger(__name__)

# telegram_user_id -> (когда спрошено `now()`, что ответили)
_cache: dict[str, tuple[float, bool | None]] = {}
#: кого сейчас перепроверяют в фоне — второй промах за это время новую не заводит
_refreshing: set[str] = set()
_cache_lock = threading.Lock()


def _spawn_thread(job) -> None:
    threading.Thread(target=job, name="bot-reach-refresh", daemon=True).start()


#: как запускается фоновая перепроверка; тесты подменяют на «выполнить сразу»
_spawn = _spawn_thread


def reset_state() -> None:
    """Забыть кэш и фоновые перепроверки — для тестов."""
    with _cache_lock:
        _cache.clear()
        _refreshing.clear()


def _ask(telegram_user_id: str, check) -> bool | None:
    result = check(
        [{"telegram_user_id": telegram_user_id}],
        token=settings.telegram_bot_token or "",
        timeout=REACH_TIMEOUT_SECONDS,
    )
    items = result.get("items") or []
    return items[0].get("reachable") if items else None


def should_show(
    *,
    is_dictor: bool,
    auth_source: str,
    shown_at: datetime | None,
    bot_reachable: bool | None,
    now: datetime,
) -> bool:
    """Роль `dictor` и (ещё ни разу не показывали ИЛИ вход через Telegram, бот молчит
    хотя бы сутки). `bot_reachable is None` ("не дозвонились") показ не подталкивает —
    это неизвестность, а не отказ.
    """
    if not is_dictor:
        return False
    if shown_at is None:
        return True
    if auth_source != "telegram":
        return False
    if bot_reachable is not False:
        return False
    return (now - shown_at) >= STALE_AFTER


def bot_reachable(
    telegram_user_id: str,
    *,
    fresh: bool = False,
    check=check_bot_reach,
    now=time.monotonic,
    spawn=None,
) -> bool | None:
    """Достижимость одного человека, с кэшем на `CACHE_TTL_SECONDS`.

    Обычный вызов никогда не ждёт Telegram: свежий кэш — ответ из него; иначе — последний
    известный ответ (или `None`) сразу, а перепроверка уходит в фон (одна на человека).
    `fresh=True` (кнопка «Проверить ещё раз») спрашивает Telegram сейчас же, с таймаутом,
    и кладёт ответ в кэш — следующий обычный вызов получит его бесплатно.
    """
    telegram_user_id = str(telegram_user_id or "").strip()
    if not telegram_user_id:
        return None

    ts = now()
    if fresh:
        value = _ask(telegram_user_id, check)
        with _cache_lock:
            _cache[telegram_user_id] = (ts, value)
        return value

    with _cache_lock:
        cached = _cache.get(telegram_user_id)
        if cached is not None and ts - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]
        known = cached[1] if cached is not None else None
        if telegram_user_id in _refreshing:
            return known
        _refreshing.add(telegram_user_id)

    def refresh() -> None:
        try:
            value = _ask(telegram_user_id, check)
            with _cache_lock:
                _cache[telegram_user_id] = (now(), value)
        except Exception:  # noqa: BLE001 — фон не должен ронять процесс; прежний ответ остаётся
            logger.exception("bot reach refresh failed for %s", telegram_user_id)
        finally:
            with _cache_lock:
                _refreshing.discard(telegram_user_id)

    try:
        (spawn or _spawn)(refresh)
    except Exception:
        # поток не завёлся — не беда: следующий вызов попробует снова
        with _cache_lock:
            _refreshing.discard(telegram_user_id)
    return known


def mark_reachable(telegram_user_id: str, *, now=time.monotonic) -> None:
    """Человек только что написал боту — значит, бот ему писать может. Без похода в Telegram."""
    telegram_user_id = str(telegram_user_id or "").strip()
    if telegram_user_id:
        with _cache_lock:
            _cache[telegram_user_id] = (now(), True)
