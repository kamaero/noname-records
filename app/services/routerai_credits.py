"""Баланс RouterAI — единственный способ узнать, сколько стоил прогон."""
from __future__ import annotations

from typing import Callable

CREDITS_URL = "https://routerai.ru/api/v1/credits"


def read_credits(log: Callable[[str], None] | None = None) -> float | None:
    """Баланс в рублях. `None` — не ответил: это не повод останавливать работу."""
    import requests

    from app.services.provider_keys import provider_key

    say = log or (lambda _message: None)
    try:
        response = requests.get(CREDITS_URL, timeout=20,
                                headers={"Authorization": f"Bearer {provider_key('routerai')}"})
    except Exception as exc:  # noqa: BLE001 — замер денег не обязан ронять работу
        say(f"! баланс не прочитан, сеть: {exc}")
        return None
    credits = None
    if response.status_code < 400:
        try:
            credits = (response.json().get("data") or {}).get("credits")
        except ValueError:
            credits = None
    if credits is None:
        say(f"! баланс не прочитан: http {response.status_code} {response.text[:120]}")
        return None
    try:
        return float(credits)
    except (TypeError, ValueError):
        say(f"! баланс не прочитан: не число ({credits!r})")
        return None
