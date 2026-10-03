"""Сбой, который прогон переживает, всё равно оставляет след в журнале.

Широкий `except … : pass` прятал такие сбои целиком: итог консилиума не дошёл до
владельца, и узнать об этом было неоткуда. Отказ отправки в Telegram — тем более: это
общая труба всех уведомлений.
"""
import logging

import requests

from app.config import settings
from app.services import telegram

TOKEN = "123456:SECRET-TOKEN"


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "telegram_notify_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", TOKEN)
    monkeypatch.setattr(settings, "owner_telegram_id", "")


def test_a_network_failure_is_logged_without_the_bot_token(monkeypatch, caplog):
    _enable(monkeypatch)

    def refuse(url, **kwargs):
        # так requests и пишет: адрес запроса — вместе с токеном — прямо в тексте ошибки
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(telegram.requests, "post", refuse)
    with caplog.at_level(logging.WARNING, logger="app.services.telegram"):
        sent = telegram.send_telegram_message(None, "текст", chat_ids=["42", "43"])

    assert sent == 0
    assert "чат 42" in caplog.text and "чат 43" in caplog.text, "один чат не срывает остальные"
    assert "ConnectionError" in caplog.text
    assert TOKEN not in caplog.text and "SECRET" not in caplog.text


def test_a_refusal_by_telegram_is_logged_with_its_code(monkeypatch, caplog):
    _enable(monkeypatch)

    class Refused:
        status_code = 403

    monkeypatch.setattr(telegram.requests, "post", lambda url, **kwargs: Refused())
    with caplog.at_level(logging.WARNING, logger="app.services.telegram"):
        assert telegram.send_telegram_message(None, "текст", chat_ids=["42"]) == 0

    assert "HTTP 403" in caplog.text


def test_a_failed_ambient_notice_is_logged_and_does_not_break_the_run(caplog):
    from app.services import ambient_engine

    def boom(text):
        raise RuntimeError("telegram down")

    result = {"status": "done", "chapter_index": 1, "tracks_done": 1, "tracks_total": 1,
              "tracks_failed": 0, "tracks_skipped": 0}
    with caplog.at_level(logging.ERROR, logger="app.services.ambient_engine"):
        ambient_engine._notify(None, boom, result)  # не бросает

    assert "не отправлен в Telegram" in caplog.text
    assert "Г1: эмбиент 1 из 1" in caplog.text
