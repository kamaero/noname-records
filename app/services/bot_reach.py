"""Кому бот может написать, а кому нет.

Вход через кнопку «Log in with Telegram» и уведомления бота — разные каналы, и их легко
спутать. Виджет работает без всякого бота: Telegram сам подтверждает личность, боту
достаточно владеть доменом. А писать человеку бот не может, пока тот сам не нажал Start:
так устроен Telegram, это защита от рассылок.

Значит человек может прекрасно входить и при этом не получать ни «ты утверждён на роль»,
ни «не нашлось трёх реплик» — молча, потому что отправка просто вернёт ноль.

`getChat` спрашивает об этом, ничего не отправляя.
"""
from __future__ import annotations

import requests

API = "https://api.telegram.org/bot{token}/getChat"


def check_bot_reach(rows: list[dict], *, token: str, timeout: int = 8) -> dict:
    """Опросить телеграм о каждой записи. Недостижимые — первыми: их и надо читать.

    `reachable=None` — не дозвонились до Telegram. Это не приговор человеку: «не смогли
    спросить» и «нельзя писать» — разные вещи, и путать их нельзя.
    """
    if not rows:
        return {"items": [], "checked": 0, "unreachable": 0, "error": ""}
    if not str(token or "").strip():
        return {"items": [], "checked": 0, "unreachable": 0, "error": "no_token"}

    url = API.format(token=token)
    items: list[dict] = []
    for row in rows:
        chat_id = str(row.get("telegram_user_id") or "")
        item = {
            "telegram_user_id": chat_id,
            "display_name": str(row.get("display_name") or ""),
            "linked_login": str(row.get("linked_login") or ""),
            "telegram_name": "",
            "username": "",
            "reachable": None,
            "reason": "",
        }
        try:
            payload = requests.get(url, params={"chat_id": chat_id}, timeout=timeout).json()
        except Exception:  # noqa: BLE001 — сеть подвела, а не человек
            item["reason"] = "не удалось спросить Telegram"
            items.append(item)
            continue
        if payload.get("ok"):
            chat = payload.get("result") or {}
            item["reachable"] = True
            item["telegram_name"] = " ".join(
                part for part in (chat.get("first_name"), chat.get("last_name")) if part
            ).strip()
            item["username"] = str(chat.get("username") or "")
        else:
            item["reachable"] = False
            item["reason"] = str(payload.get("description") or "")
        items.append(item)

    items.sort(key=lambda entry: (entry["reachable"] is True, entry["display_name"].lower()))
    return {
        "items": items,
        "checked": len(items),
        "unreachable": sum(1 for entry in items if entry["reachable"] is False),
        "error": "",
    }
