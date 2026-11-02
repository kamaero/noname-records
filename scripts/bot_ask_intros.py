#!/usr/bin/env python3
"""Разово: спросить «Как вас зовут?» людей из базы голосов, у которых нет учётки и которым
бот уже может писать (нажали Start). Сначала --list владельцу, --send — только после его «да».

    sudo -u www-data .venv312/bin/python3 -m scripts.bot_ask_intros --list
    sudo -u www-data .venv312/bin/python3 -m scripts.bot_ask_intros --send
"""
from __future__ import annotations

import argparse
import json


def main(argv=None, *, check=None, send=None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--list", action="store_true")
    mode.add_argument("--send", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    from app import db as app_db
    from app.config import settings
    from app.services import bot_intro
    from app.services.bot_reach import check_bot_reach
    from app.services.telegram import call_bot_api
    from app.time_utils import utcnow_naive

    check = check or check_bot_reach
    send = send or call_bot_api
    speakers = json.load(open(args.json or bot_intro.VOICES_JSON, encoding="utf-8"))["speakers"]
    with app_db.SessionLocal() as db:
        rows = [{"telegram_user_id": str(s.get("user_id") or s.get("id") or ""), "display_name": str(s.get("name") or "")}
                for s in speakers]
        rows = [r for r in rows if bot_intro.may_introduce(db, r["telegram_user_id"])]
        items = [i for i in check(rows, token=settings.telegram_bot_token)["items"] if i.get("reachable") is True]
        for item in items:
            print(f"{item['telegram_user_id']}\t{item.get('display_name', '')}\t@{item.get('username', '')}")
        print(f"всего: {len(items)} (из {len(rows)} без учётки)")
        if args.list:
            return 0
        asked = 0
        for item in items:
            bot_intro.ask(db, item["telegram_user_id"], now=utcnow_naive())
            db.commit()
            if send("sendMessage", {"chat_id": item["telegram_user_id"], "text": bot_intro.ASK_FIRST}):
                asked += 1
        print(f"спрошено: {asked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
