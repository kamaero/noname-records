"""Сигнал не копирует файлы: он лишь будит единственный штатный цикл зеркала."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time

from app.config import settings as app_settings

logger = logging.getLogger(__name__)
_POLL_SECONDS = 1.0
_TTL_SECONDS = 600
_CONSUME = """
local value = redis.call('GET', KEYS[1])
if value then redis.call('DEL', KEYS[1]) end
return value
"""


def _key(settings) -> str:
    # Разные установки могут делить Redis, но не должны будить чужое зеркало.
    storage_path = os.path.realpath(os.path.abspath(str(settings.audio_storage_path)))
    identity = json.dumps([storage_path, str(getattr(settings, "database_url", ""))],
        ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return "audio-mirror:wake:" + hashlib.sha256(identity).hexdigest()[:24]


def _client(settings):
    from redis import Redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    client = Redis.from_url(
        settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2,
        retry_on_timeout=False,
    )
    # from_url отдаёт query-параметрам приоритет над kwargs. Фиксируем уже
    # разобранный pool до первого соединения, иначе URL растянет паузу на минуты.
    client.connection_pool.connection_kwargs.update(
        socket_connect_timeout=0.2, socket_timeout=0.2, retry_on_timeout=False,
        retry_on_error=[], retry=Retry(NoBackoff(), 0),
    )
    return client


def _close(client) -> None:
    if client is not None:
        try:
            client.close()
        except Exception:
            logger.debug("Не удалось закрыть соединение сигнала зеркала")


def request_mirror_wakeup(*, settings=None, redis_factory=None) -> bool:
    """Вызывать после commit: один флаг объединяет сколько угодно новых файлов."""
    settings = settings or app_settings
    if str(getattr(settings, "seat_mode", "studio")).lower() == "one" or not getattr(settings, "audio_nas_path", ""):
        return False
    client = None
    try:
        client = (redis_factory or _client)(settings)
        return bool(client.set(_key(settings), "1", ex=_TTL_SECONDS))
    except Exception:
        # Redis не участвует в корректности копирования: останется очередной период.
        logger.debug("Сигнал зеркала недоступен; остаётся штатный период")
        return False
    finally:
        _close(client)


def wait_for_mirror_wakeup(
    seconds: float, *, settings=None, redis_factory=None, sleep=None, monotonic=None,
) -> bool:
    """Один сигнал — один проход; даже непрерывные сигналы не создают busy loop."""
    settings = settings or app_settings
    sleep = sleep or time.sleep
    monotonic = monotonic or time.monotonic
    if str(getattr(settings, "seat_mode", "studio")).lower() == "one" or not getattr(settings, "audio_nas_path", ""):
        # Настольной установке Redis не нужен даже при наличии настроенного NAS.
        # Прежний единственный цикл продолжит работать по своему интервалу.
        sleep(max(0.0, float(seconds)))
        return False
    deadline = monotonic() + max(0.0, float(seconds))
    client = None
    try:
        # Минимальная пауза действует и для уже лежащего флага: постоянный поток
        # загрузок не должен превращать единственный цикл в непрерывный опрос NAS.
        remaining = deadline - monotonic()
        if remaining <= 0:
            return False
        sleep(min(_POLL_SECONDS, remaining))
        client = (redis_factory or _client)(settings)
        while True:
            if client.eval(_CONSUME, 1, _key(settings)):
                return True
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            sleep(min(_POLL_SECONDS, remaining))
    except Exception:
        # Ошибка Redis не меняет прежнюю периодичность и не запускает копирование.
        remaining = deadline - monotonic()
        if remaining > 0:
            sleep(remaining)
        return False
    finally:
        _close(client)
