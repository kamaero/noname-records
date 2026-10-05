"""Проверка ключа и баланс — одним коротким настоящим запросом к провайдеру.

Ответ — словами для человека: «ключ не подходит» понятнее, чем «401». Ключ не попадает
в текст ответа даже тогда, когда провайдер повторяет его в сообщении об ошибке.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

from app.config import settings

TIMEOUT = 15
WORDS = {"ok": "Работает.", "bad_key": "Ключ не подходит — проверьте, что скопирован целиком.",
         "no_money": "На счёте у провайдера нет денег.", "unreachable": "Сервис не отвечает — попробуйте позже.",
         "error": "Ответ не похож на ответ провайдера — проверьте адрес сервиса или повторите позже."}
_NO_MONEY_WORDS = ("insufficient", "balance", "credit", "quota", "payment")


@dataclass(frozen=True)
class CheckResult:
    status: str
    detail: str


def _probe(provider: str, key: str):
    if provider == "claude":
        return "GET", f"{settings.claude_base_url.rstrip('/')}/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    if provider == "elevenlabs":
        return "GET", "https://api.elevenlabs.io/v1/user/subscription", {"xi-api-key": key}
    base = {"deepseek": "https://api.deepseek.com", "openai": settings.openai_base_url,
            "routerai": settings.routerai_base_url, "openrouter": settings.openrouter_base_url}[provider]
    return "GET", f"{base.rstrip('/')}/models", {"Authorization": f"Bearer {key}"}


def _status(code: int) -> str:
    if code < 300:
        return "ok"
    if code in (401, 403):
        return "bad_key"
    if code == 402:
        return "no_money"
    return "unreachable"


def check_key(provider: str, key: str, *, http=requests) -> CheckResult:
    method, url, headers = _probe(provider, key)
    try:
        resp = http.get(url, headers=headers, timeout=TIMEOUT)
    except requests.RequestException:
        return CheckResult("unreachable", WORDS["unreachable"])
    code = int(getattr(resp, "status_code", 0) or 0)
    status = _status(code)
    if status == "ok" and not _looks_like(provider, resp):
        # 200 от прокси или страницы-заглушки — не доказательство, что ключ принят
        status = "error"
    elif code in (400, 403, 429) and any(w in str(getattr(resp, "text", "") or "").lower() for w in _NO_MONEY_WORDS):
        status = "no_money"
    return CheckResult(status, WORDS[status])


def _looks_like(provider: str, resp) -> bool:
    """Тело — настоящий ответ провайдера: список моделей или карточка подписки ElevenLabs."""
    try:
        data = resp.json()
    except (ValueError, TypeError, AttributeError):
        return False
    if not isinstance(data, dict):
        return False
    if provider == "elevenlabs":
        return "character_limit" in data or "tier" in data
    return isinstance(data.get("data"), list)


_BALANCE_URLS = {"deepseek": "https://api.deepseek.com/user/balance",
                 "openrouter": "https://openrouter.ai/api/v1/credits",
                 "routerai": "https://routerai.ru/api/v1/credits",
                 "elevenlabs": "https://api.elevenlabs.io/v1/user/subscription"}


def _amount(provider: str, data: dict) -> tuple[float, str]:
    if provider == "deepseek":
        info = (data.get("balance_infos") or [{}])[0]
        return float(info.get("total_balance") or 0), "$" if info.get("currency") == "USD" else "₽"
    if provider == "openrouter":
        d = data.get("data") or {}
        return float(d.get("total_credits") or 0) - float(d.get("total_usage") or 0), "$"
    if provider == "routerai":
        # то же поле, что читает счётчик прогонов (`routerai_credits`)
        return float((data.get("data") or {})["credits"]), "₽"
    return float(data.get("character_limit") or 0) - float(data.get("character_count") or 0), "символов"


def balance(provider: str, key: str, *, http=requests) -> dict:
    empty = {"provider": provider, "available": False, "amount": None, "unit": "", "detail": ""}
    if provider not in _BALANCE_URLS:
        return {**empty, "detail": "провайдер не сообщает баланс"}
    _m, _u, headers = _probe(provider, key)
    try:
        resp = http.get(_BALANCE_URLS[provider], headers=headers, timeout=TIMEOUT)
        amount, unit = _amount(provider, resp.json()) if resp.status_code < 300 else (None, "")
    except (requests.RequestException, ValueError, TypeError, AttributeError, IndexError, KeyError):
        return {**empty, "detail": "нет данных"}
    if amount is None:
        return {**empty, "detail": "нет данных"}
    return {"provider": provider, "available": True, "amount": round(amount, 2), "unit": unit, "detail": ""}
