from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config import settings

# Единый экземпляр Limiter, переиспользуемый в main.py (глобальный лимит +
# middleware) и в auth_routes.py (строгий per-route лимит на вход). Вынесен в
# отдельный модуль, чтобы избежать циклического импорта между ними.
_rate_limit_storage_uri = (
    str(settings.api_rate_limit_storage_uri or "").strip()
    or str(settings.redis_url or "").strip()
    or "memory://"
)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[str(settings.api_rate_limit_default or "240/minute")],
    storage_uri=_rate_limit_storage_uri,
    headers_enabled=True,
    in_memory_fallback=[str(settings.api_rate_limit_default or "240/minute")],
    in_memory_fallback_enabled=True,
    enabled=bool(settings.api_rate_limit_enabled),
)
