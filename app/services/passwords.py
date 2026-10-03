"""Хеши паролей: PBKDF2-HMAC-SHA256 в формате passlib — `$pbkdf2-sha256$<раунды>$<соль>$<хеш>`.

Раньше это делал passlib (`CryptContext(schemes=["pbkdf2_sha256"])`). Он не обновлялся с
2020 года и опирается на модуль `crypt`, которого в Python 3.13 нет. Сама схема —
стандартный PBKDF2 из `hashlib`, поэтому она здесь, без зависимостей, и формат строки тот
же до байта: все хеши, что уже лежат в базе и в `ADMIN_PASSWORD_HASH`, проверяются как
раньше, а новые принимает и passlib — откат кода никого не запрёт.

Соль и хеш — «адаптированный base64» passlib: обычный base64 без `=` и с `.` вместо `+`.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets

SCHEME = "pbkdf2-sha256"
#: столько же, сколько по умолчанию давал passlib 1.7.4 — время входа не меняется
DEFAULT_ROUNDS = 29000
SALT_BYTES = 16
#: потолок раундов при проверке: чужой хеш с миллиардом раундов не должен вешать вход
MAX_ROUNDS = 10_000_000


def _ab64_encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii").rstrip("=").replace("+", ".")


def _ab64_decode(text: str) -> bytes:
    text = text.replace(".", "+")
    return base64.b64decode(text + "=" * (-len(text) % 4), validate=True)


def hash_password(password: str, *, rounds: int = DEFAULT_ROUNDS) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, rounds)
    return f"${SCHEME}${rounds}${_ab64_encode(salt)}${_ab64_encode(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Подходит ли пароль к хешу. Битый или чужой хеш — «нет», а не исключение."""
    parts = str(stored or "").split("$")
    # "$pbkdf2-sha256$29000$соль$хеш" → ["", схема, раунды, соль, хеш]
    if len(parts) != 5 or parts[0] or parts[1] != SCHEME:
        return False
    try:
        rounds = int(parts[2])
        salt = _ab64_decode(parts[3])
        expected = _ab64_decode(parts[4])
    except (ValueError, binascii.Error):
        return False
    if not 0 < rounds <= MAX_ROUNDS or not expected:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, rounds, dklen=len(expected))
    return hmac.compare_digest(digest, expected)
