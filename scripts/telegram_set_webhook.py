#!/usr/bin/env python3
"""Регистрация webhook бота. Боту нужен сайт с HTTPS (сервер с доменом), адрес — APP_BASE_URL:

    docker compose exec web python -m scripts.telegram_set_webhook          # поставить
    docker compose exec web python -m scripts.telegram_set_webhook --info   # посмотреть
    docker compose exec web python -m scripts.telegram_set_webhook --delete # снять
    docker compose exec web python -m scripts.telegram_set_webhook --logout # выйти из базы

Куда ходит — решает `TELEGRAM_API_BASE` (облако Telegram или свой узел Bot API).
`TELEGRAM_WEBHOOK_URL` переопределяет адрес, который узел Bot API передаёт приложению.
Токен и секрет — из .env; в вывод не попадают.
"""
import argparse
import os
import sys

import requests


def webhook_url() -> str:
    from app.services import studio
    from app.services.telegram import is_local_bot_api

    explicit = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()
    if explicit:
        return explicit
    if is_local_bot_api():
        return "http://web:8000/api/telegram/webhook"
    return studio.site_url("/api/telegram/webhook")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--info", action="store_true")
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--logout", action="store_true",
                        help="logOut у текущей базы — перед переходом на другую (Telegram ждёт 10 минут до возврата)")
    pending = parser.add_mutually_exclusive_group()
    pending.add_argument("--keep-pending", action="store_true",
                         help="не выбрасывать сообщения, накопленные, пока вебхука не было")
    pending.add_argument("--drop-pending", action="store_true")
    args = parser.parse_args(argv)

    from app.config import settings
    from app.services.telegram import bot_api_url, is_local_bot_api

    token = (settings.telegram_bot_token or "").strip()
    secret = (settings.telegram_webhook_secret or "").strip()
    if not token:
        print("нет TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 2
    if args.info:
        info = requests.get(bot_api_url("getWebhookInfo", token), timeout=10).json().get("result", {})
        for key in ("url", "pending_update_count", "last_error_date", "last_error_message", "allowed_updates"):
            print(f"{key}: {info.get(key)}")
        return 0
    if args.delete:
        print(requests.post(bot_api_url("deleteWebhook", token), timeout=10).json().get("description"))
        return 0
    if args.logout:
        resp = requests.post(bot_api_url("logOut", token), timeout=10).json()
        print(resp.get("description") or ("ok" if resp.get("ok") else "отказ"))
        return 0 if resp.get("ok") else 1
    if not secret:
        print("нет TELEGRAM_WEBHOOK_SECRET", file=sys.stderr)
        return 2
    # На своём узле сохраняем накопленные сообщения. В облаке прежнее умолчание —
    # свежий вебхук без старых хвостов; явные флаги имеют приоритет.
    drop = not is_local_bot_api()
    if args.keep_pending:
        drop = False
    if args.drop_pending:
        drop = True
    resp = requests.post(bot_api_url("setWebhook", token), timeout=10, json={
        "url": webhook_url(), "secret_token": secret, "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": drop,
    }).json()
    print(resp.get("description"))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
