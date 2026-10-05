"""Ключи нейросетей: сначала введённые на сайте, потом .env.

Одна точка для всего приложения — иначе ключ, сменённый на сайте, подхватила бы разметка,
а консилиум продолжал бы ходить со старым из .env. Кэш на 30 секунд: воркеры видят новый
ключ без перезапуска, но не ходят в базу на каждый абзац.
"""
from __future__ import annotations

import base64
import time

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.exc import SQLAlchemyError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import env_value, settings
from app.db import SessionLocal
from app.models import ProviderKey
from app.time_utils import utcnow_naive

PROVIDERS = ("deepseek", "claude", "openai", "routerai", "openrouter", "elevenlabs")
ENV_NAMES = {
    "deepseek": ("DEEPSEEK_API_KEY",), "claude": ("CLAUDE_API_KEY",),
    "openai": ("OPENAI_API_KEY", "LLM_API_KEY"), "routerai": ("ROUTERAI_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",), "elevenlabs": ("ELEVENLABS_API_KEY",),
    "zai": ("ZAI_API_KEY",), "azure": ("AZURE_SPEECH_KEY",),
}
#: то же значение, прочитанное из окружения при старте: тесты и старый код задают его так
SETTINGS_ATTRS = {"deepseek": "deepseek_api_key", "claude": "claude_api_key", "openai": "openai_api_key",
                  "routerai": "routerai_api_key", "openrouter": "openrouter_api_key",
                  "elevenlabs": "elevenlabs_api_key", "zai": "zai_api_key", "azure": "azure_speech_key"}
CACHE_SECONDS = 30
_CACHE: dict[str, tuple[float, str]] = {}


def _now() -> float:
    return time.monotonic()


def clear_cache() -> None:
    _CACHE.clear()


def _fernet() -> Fernet:
    """Ключ шифрования — KEYS_ENCRYPTION_KEY; нет его — выводится из SECRET_KEY, чтобы
    установки до этой версии работали без шага руками."""
    explicit = (settings.keys_encryption_key or "").strip()
    if explicit:
        return Fernet(explicit.encode())
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b"noname-records/provider-keys").derive((settings.secret_key or "").encode())
    return Fernet(base64.urlsafe_b64encode(raw))


def _decrypt(row: ProviderKey) -> str | None:
    try:
        return _fernet().decrypt(row.ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def _env_key(name: str) -> str:
    for env_name in ENV_NAMES.get(name, ()):
        value = env_value(env_name)
        if value:
            return value
    return str(getattr(settings, SETTINGS_ATTRS.get(name, ""), "") or "").strip()


def provider_key(name: str) -> str:
    hit = _CACHE.get(name)
    if hit and _now() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = ""
    if name in PROVIDERS:
        try:
            with SessionLocal() as db:
                row = db.get(ProviderKey, name)
                if row is not None and row.ciphertext:
                    value = _decrypt(row) or ""
        except SQLAlchemyError:
            # Таблицы ещё нет (до миграции) — ключ сайта ничем не отличается от отсутствующего.
            value = ""
    value = value or _env_key(name)
    _CACHE[name] = (_now(), value)
    return value


def set_key(db, name: str, value: str, *, actor: str) -> None:
    if name not in PROVIDERS:
        raise ValueError(f"провайдер {name} задаётся только в .env")
    clean = "".join(str(value or "").split())
    if not clean:
        raise ValueError("пустой ключ")
    row = db.get(ProviderKey, name) or ProviderKey(provider=name)
    row.ciphertext = _fernet().encrypt(clean.encode()).decode()
    row.last4 = clean[-4:]
    row.check_status, row.check_detail, row.checked_at = "", "", None
    row.updated_by = actor
    db.add(row)
    db.flush()
    _CACHE.pop(name, None)


def delete_key(db, name: str, *, actor: str) -> None:
    row = db.get(ProviderKey, name)
    if row is not None:
        db.delete(row)
        db.flush()
    _CACHE.pop(name, None)


def record_check(db, name: str, status: str, detail: str) -> None:
    row = db.get(ProviderKey, name)
    if row is None:
        return
    row.check_status, row.check_detail, row.checked_at = status, detail[:200], utcnow_naive()
    db.flush()


def key_info(db, name: str) -> dict:
    row = db.get(ProviderKey, name)
    readable = row is not None and bool(row.ciphertext) and _decrypt(row) is not None
    source = "site" if readable else ("env" if _env_key(name) else "none")
    return {
        "provider": name, "source": source, "last4": row.last4 if readable else "",
        "unreadable": row is not None and bool(row.ciphertext) and not readable,
        "check_status": row.check_status if row else "", "check_detail": row.check_detail if row else "",
        "checked_at": row.checked_at.isoformat() + "Z" if row and row.checked_at else "",
    }
