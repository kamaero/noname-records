#!/usr/bin/env python3
"""Регистрация webhook бота. Боту нужен сайт с HTTPS (сервер с доменом), адрес — APP_BASE_URL:

    docker compose exec web python -m scripts.telegram_set_webhook          # поставить
    docker compose exec web python -m scripts.telegram_set_webhook --info   # посмотреть
    docker compose exec web python -m scripts.telegram_set_webhook --delete # снять

Токен и секрет — из .env; в вывод не попадают.
"""
import argparse
import sys

import requests



def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--info", action="store_true")
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args(argv)

    from app.config import settings
    from app.services import studio

    url = studio.site_url("/api/telegram/webhook")

    token = (settings.telegram_bot_token or "").strip()
    secret = (settings.telegram_webhook_secret or "").strip()
    if not token:
        print("нет TELEGRAM_BOT_TOKEN", file=sys.stderr)
        return 2
    api = f"https://api.telegram.org/bot{token}"
    if args.info:
        info = requests.get(f"{api}/getWebhookInfo", timeout=10).json().get("result", {})
        for key in ("url", "pending_update_count", "last_error_date", "last_error_message", "allowed_updates"):
            print(f"{key}: {info.get(key)}")
        return 0
    if args.delete:
        print(requests.post(f"{api}/deleteWebhook", timeout=10).json().get("description"))
        return 0
    if not secret:
        print("нет TELEGRAM_WEBHOOK_SECRET", file=sys.stderr)
        return 2
    resp = requests.post(f"{api}/setWebhook", timeout=10, json={
        "url": url, "secret_token": secret, "allowed_updates": ["message", "callback_query"],
        "drop_pending_updates": True,
    }).json()
    print(resp.get("description"))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
