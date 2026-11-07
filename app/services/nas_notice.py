"""Тревога по готовому флагу сторожа: отправка никогда не обращается к NFS."""
from __future__ import annotations

import logging
import time

from app.services.telegram import send_telegram_message, telegram_system_chat_ids

logger = logging.getLogger(__name__)

_MESSAGES = {
    "offline": "⚠️ The NAS mirror is unavailable. Copying is paused; the server still accepts recordings.",
    "unknown": "⚠️ The NAS mirror status is unknown: the health probe is not updating. Please check background tasks.",
    "online": "✅ The NAS mirror is available again. Copying will continue in the regular cycle.",
}


class NasTransitionNotice:
    """Подавление повторов живёт до рестарта процесса; после него сбой сообщается заново."""

    def __init__(self, *, grace_seconds: float, retry_seconds: float = 300, clock=None):
        self._clock = clock or time.monotonic
        self._started = self._clock()
        self._grace = max(0, grace_seconds)
        self._retry = max(1, retry_seconds)
        self._state: str | None = None
        self._message = ""
        self._delivered: set[str] = set()
        self._attempted: dict[str, float] = {}

    def observe(self, db, online: bool | None) -> None:
        now = self._clock()
        # При старте сторож ещё мог не получить процессор. После первого известного
        # ответа None уже означает протухшую проверку, а не обычный старт.
        if online is None and self._state is None and now - self._started < self._grace:
            return
        state = "unknown" if online is None else "online" if online else "offline"
        if state != self._state:
            self._message = "" if self._state is None and state == "online" else _MESSAGES[state]
            self._state = state
            self._delivered.clear()
            self._attempted.clear()
        if not self._message:
            return
        targets = telegram_system_chat_ids(db)
        self._delivered.intersection_update(targets)
        self._attempted = {chat: at for chat, at in self._attempted.items() if chat in targets}
        for chat in targets:
            if chat in self._delivered:
                continue
            previous = self._attempted.get(chat)
            if previous is not None and now - previous < self._retry:
                continue
            self._attempted[chat] = now
            try:
                if send_telegram_message(db, self._message, chat_ids=[chat], direct=True):
                    self._delivered.add(chat)
            except Exception as exc:
                # Один отказ не скрывает тревогу от второго адресата. Текст исключения
                # может содержать URL с токеном, поэтому наружу идёт только его тип.
                logger.warning("Не удалось отправить состояние NAS: %s", type(exc).__name__)
