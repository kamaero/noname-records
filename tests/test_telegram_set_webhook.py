"""Скрипт вебхука и свой узел Bot API (21.10).

Свой узел сам слушает Telegram и отдаёт обновления приложению напрямую, мимо nginx. При
переключении бот молчит несколько минут, и Telegram копит сообщения — прежний скрипт
выбрасывал накопленное (`drop_pending_updates`), и всё, что дикторы прислали за эти
минуты, пропало бы.
"""
import pytest

from app.config import settings
from scripts import telegram_set_webhook as script


class _Answer:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture()
def calls(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "telegram_bot_token", "SECRET-TOKEN")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "S")

    def post(url, **kw):
        sent.append((url, kw.get("json")))
        return _Answer({"ok": True, "description": "Webhook was set"})

    monkeypatch.setattr(script.requests, "post", post)
    monkeypatch.setattr(script.requests, "get", lambda url, **kw: _Answer({"ok": True, "result": {}}))
    return sent


def test_local_mode_points_at_the_app_and_keeps_pending(calls, monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_base", "http://127.0.0.1:8081")

    assert script.main([]) == 0

    [(url, body)] = calls
    assert url == "http://127.0.0.1:8081/botSECRET-TOKEN/setWebhook"
    assert body["url"] == "http://web:8000/api/telegram/webhook"
    assert body["drop_pending_updates"] is False and body["secret_token"] == "S"


def test_cloud_mode_keeps_todays_behaviour(calls, monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_base", "")
    monkeypatch.setattr(settings, "app_base_url", "https://studio.example")

    assert script.main([]) == 0

    [(url, body)] = calls
    assert url == "https://api.telegram.org/botSECRET-TOKEN/setWebhook"
    assert body["url"] == "https://studio.example/api/telegram/webhook" and body["drop_pending_updates"] is True


def test_keep_pending_in_cloud(calls, monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_base", "")

    script.main(["--keep-pending"])

    assert calls[0][1]["drop_pending_updates"] is False


def test_logout_calls_logout_on_the_current_base(calls, monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_base", "")

    assert script.main(["--logout"]) == 0

    assert calls[0][0] == "https://api.telegram.org/botSECRET-TOKEN/logOut"


@pytest.mark.parametrize("args", [[], ["--logout"], ["--info"], ["--delete"]])
@pytest.mark.parametrize("base", ["", "http://127.0.0.1:8081"])
def test_token_is_not_printed(calls, monkeypatch, capsys, args, base):
    monkeypatch.setattr(settings, "telegram_api_base", base)

    script.main(args)

    assert "SECRET-TOKEN" not in capsys.readouterr().out
