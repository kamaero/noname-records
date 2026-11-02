"""Адрес Bot API — из одной настройки (21.10, свой узел Telegram Bot API).

Пока в `.env` нет `TELEGRAM_API_BASE`, бот ходит в облако Telegram, как всегда. Локальный
узел снимает предел облака в 20 МБ на файл, и переключение должно быть одной строкой
настроек, а не правкой шести мест в коде.
"""
from app.config import settings
from app.services import telegram


def test_cloud_by_default(monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_base", "https://api.telegram.org")
    monkeypatch.setattr(settings, "telegram_bot_token", "T")

    assert telegram.bot_api_url("getMe") == "https://api.telegram.org/botT/getMe"
    assert telegram.is_local_bot_api() is False


def test_local_base_and_trailing_slash(monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_base", "http://127.0.0.1:8081/")
    monkeypatch.setattr(settings, "telegram_bot_token", "T")

    assert telegram.bot_api_url("sendMessage") == "http://127.0.0.1:8081/botT/sendMessage"
    assert telegram.is_local_bot_api() is True


def test_an_empty_setting_means_the_cloud(monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_base", "")
    monkeypatch.setattr(settings, "telegram_bot_token", "T")

    assert telegram.bot_api_url("getMe") == "https://api.telegram.org/botT/getMe"
    assert telegram.is_local_bot_api() is False


def test_no_module_hardcodes_the_cloud():
    """Новое место с зашитым адресом молча ходило бы мимо локального узла."""
    for path in ("app/services/telegram.py", "app/services/bot_reach.py", "scripts/telegram_set_webhook.py"):
        text = open(path, encoding="utf-8").read()
        allowed = 1 if path.endswith("services/telegram.py") else 0
        assert text.count("api.telegram.org") <= allowed, path
