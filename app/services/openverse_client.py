"""Каталог возвращает сведения, но не гарантирует права или доступность файла."""
from __future__ import annotations

import copy
import ipaddress
import math
import threading
import time
import uuid
from urllib.parse import urlsplit

import requests

BASE_URL = "https://api.openverse.org/v1/audio/"
ALLOWED_LICENSES = {"cc0", "pdm", "by", "by-sa"}


class OpenverseError(Exception):
    def __init__(self, code="openverse_unavailable", status=503, retry_after=None):
        self.code, self.status, self.retry_after = code, status, retry_after
        super().__init__(code)


def public_url(value):
    """Ссылки открывает браузер; сервер не загружает введённые пользователем адреса."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
        return ""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if parsed.scheme not in {"https", "http"} or not host or parsed.username or parsed.password:
            return ""
        if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local", ".internal")):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            if "." not in host or "\\" in host:
                return ""
        _ = parsed.port
        return value
    except ValueError:
        return ""


def audio_id(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("bad_openverse_id") from None


def _text(raw, name, limit=1000):
    value = raw.get(name)
    return value[:limit] if isinstance(value, str) else ""


def normalize_audio(raw):
    if not isinstance(raw, dict):
        return None
    try:
        identity = audio_id(raw.get("id"))
    except ValueError:
        return None
    license = _text(raw, "license", 40).lower()
    version = _text(raw, "license_version", 20)
    license_url = public_url(raw.get("license_url"))
    recognized = license in ALLOWED_LICENSES and bool(license_url)
    if license in {"by", "by-sa"} and version not in {"1.0", "2.0", "2.5", "3.0", "4.0"}:
        recognized = False
    if license in {"cc0", "pdm"} and version != "1.0":
        recognized = False
    if license in {"by", "by-sa"}:
        expected = f"/licenses/{license}/{version}/"
    else:
        expected = f"/publicdomain/{'zero' if license == 'cc0' else 'mark'}/1.0/"
    if recognized:
        parsed = urlsplit(license_url)
        recognized = parsed.hostname in {"creativecommons.org", "www.creativecommons.org"} and parsed.path.startswith(expected)
    rights = "share_alike" if recognized and license == "by-sa" else "compatible" if recognized else "unknown"
    # duration у Openverse — миллисекунды, а в нашем UI и старых треках — секунды.
    duration = raw.get("duration")
    duration = duration / 1000 if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration >= 0 else None
    full_url = public_url(raw.get("url"))
    preview_url = full_url
    provider = _text(raw, "provider", 100).lower()
    # Freesound выдаёт в url MP3-превью; alt_files требует токена Freesound.
    # Не называем такой адрес исходным файлом и не обещаем его скачивание.
    preview_only = "/previews/" in full_url or provider == "freesound"
    download_url = "" if preview_only else full_url
    alternatives = raw.get("alt_files")
    if isinstance(alternatives, list):
        for alternate in alternatives:
            if not isinstance(alternate, dict):
                continue
            url = public_url(alternate.get("url"))
            if url and "/previews/" in url and not preview_url:
                preview_url = url
    return {"id": identity, "external_id": identity, "source": "openverse",
            "title": _text(raw, "title", 300), "author": _text(raw, "creator", 300),
            "provider": _text(raw, "provider", 100), "source_url": public_url(raw.get("foreign_landing_url")),
            "license": license, "license_version": version, "license_url": license_url,
            "attribution": _text(raw, "attribution", 10000), "duration_seconds": duration,
            "category": _text(raw, "category", 80), "preview_url": preview_url or full_url,
            "download_url": download_url, "rights_status": rights,
            "rights_warning": "Verify attribution and terms on the source page." if rights == "compatible" else
                "ShareAlike: separately check compatibility with the finished project." if rights == "share_alike" else
                "The license has not been confirmed for commercial use."}


class OpenverseClient:
    def __init__(self, *, transport=None, ttl=300, clock=time.monotonic):
        self.transport = transport or requests.get
        self.ttl, self.clock = ttl, clock
        self._cache = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(3)

    def _get(self, path, params=None):
        key = (path, tuple(sorted((params or {}).items())))
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > self.clock():
                return copy.deepcopy(cached[1])
        if not self._slots.acquire(timeout=1):
            raise OpenverseError("openverse_busy", 503)
        try:
            response = self.transport(BASE_URL + path, params=params or {}, timeout=(4, 12),
                                      allow_redirects=False, headers={"User-Agent": "NonameRecords/1.0"})
            if response.status_code == 429:
                retry = str(response.headers.get("Retry-After", ""))
                raise OpenverseError("openverse_rate_limited", 429, retry if retry.isdigit() else "60")
            if response.status_code == 404:
                raise OpenverseError("openverse_not_found", 404)
            if response.status_code != 200:
                raise OpenverseError()
            if len(response.content) > 2 * 1024 * 1024:
                raise OpenverseError("openverse_bad_response", 502)
            payload = response.json()
            if not isinstance(payload, dict):
                raise OpenverseError("openverse_bad_response", 502)
        except (requests.RequestException, ValueError, TypeError) as exc:
            raise OpenverseError() from exc
        finally:
            self._slots.release()
        with self._lock:
            now = self.clock()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
            if len(self._cache) >= 256:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (now + self.ttl, payload)
        return copy.deepcopy(payload)

    def search(self, query, page=1, layer="music"):
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 200 or type(page) is not int or not 1 <= page <= 100:
            raise ValueError("bad_search")
        if layer not in {"music", "ambience", "fx"}:
            raise ValueError("bad_layer")
        params = {"q": query.strip(), "page": page, "page_size": 8,
                  "license_type": "commercial,modification", "license": "cc0,pdm,by,by-sa"}
        # Категории провайдера приблизительны: ручной запрос остаётся доступным.
        payload = self._get("", params)
        if not isinstance(payload.get("results"), list):
            raise OpenverseError("openverse_bad_response", 502)
        results = [normalize_audio(row) for row in payload["results"]]
        results = [row for row in results if row and row["rights_status"] != "unknown"]
        total = payload.get("page_count", page)
        total = max(page, min(total, 100)) if type(total) is int else page
        return {"results": results, "page": page, "page_count": total}

    def detail(self, identity):
        row = normalize_audio(self._get(audio_id(identity) + "/"))
        if row is None or row["external_id"] != audio_id(identity):
            raise OpenverseError("openverse_bad_response", 502)
        if row["rights_status"] == "unknown":
            raise ValueError("unsupported_license")
        return row


client = OpenverseClient()
