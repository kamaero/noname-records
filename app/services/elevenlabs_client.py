"""HTTP-клиент ElevenLabs Music (`POST /v1/music`) — генерация фонового трека.

Транспорт подменяемый: `transport(url, headers, body, timeout) -> (status, headers, body)`.
По умолчанию — `urllib`, сеть настоящая. В тестах транспорт ВСЕГДА подменён — ни один тест
не должен ходить в ElevenLabs. Ключ (`settings.elevenlabs_api_key`) никогда не попадает
в текст исключений, логи и репры — только заголовок запроса.
"""
from __future__ import annotations

import errno
import http.client
import json
import socket
import urllib.error
from typing import Callable

MUSIC_URL = "https://api.elevenlabs.io/v1/music"
OUTPUT_FORMAT = "mp3_44100_192"
MODEL_ID = "music_v2"

Transport = Callable[[str, dict, bytes, float], tuple[int, dict, bytes]]


class ElevenLabsError(Exception):
    """Прочие 4xx ElevenLabs: код ответа и короткий текст тела (без ключа)."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class QuotaExhausted(ElevenLabsError):
    """402, 429, либо 401 с «quota»/«limit» в теле — квота исчерпана, повторять бессмысленно."""


class BadKey(ElevenLabsError):
    """401/403 без слов о квоте — ключ отклонён; повторять бессмысленно, прогон стоит."""


class ElevenLabsUnavailable(ElevenLabsError):
    """Повторить можно: запрос не ушёл (DNS, отказ в соединении, сеть недоступна) — `sent=False`,
    либо ElevenLabs ответил 5xx — `sent=True`, трек не выдан и не оплачен."""

    def __init__(self, message: str, *, status: int | None = None, sent: bool = False) -> None:
        super().__init__(message, status=status)
        self.sent = sent


class ElevenLabsInterrupted(ElevenLabsError):
    """Запрос мог дойти до ElevenLabs, а ответ оборвался (таймаут ожидания, обрыв при чтении
    тела). Трек мог быть оплачен — повторять НЕЛЬЗЯ, иначе двойная оплата."""


#: ошибки соединения, при которых запрос точно не ушёл: имя не разрешилось, порт закрыт,
#: сети или хоста не видно. Всё прочее — «могло дойти».
_NOT_SENT_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EHOSTDOWN}


def _never_sent(exc: BaseException | None) -> bool:
    if isinstance(exc, (socket.gaierror, ConnectionRefusedError)):
        return True
    return isinstance(exc, OSError) and not isinstance(exc, TimeoutError) \
        and getattr(exc, "errno", None) in _NOT_SENT_ERRNOS


def _network_error(exc: BaseException) -> ElevenLabsError:
    """Сетевой сбой → «повторить можно» (запрос не ушёл) или «оборвался» (мог дойти)."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    kind = type(reason).__name__ if isinstance(reason, BaseException) else str(reason)[:80]
    if _never_sent(reason if isinstance(reason, BaseException) else None):
        return ElevenLabsUnavailable(f"сеть недоступна: {kind}", sent=False)
    return ElevenLabsInterrupted(f"ответ ElevenLabs оборвался: {kind}")


def _is_quota_status(status: int, body_text: str) -> bool:
    if status in (402, 429):
        return True
    if status == 401:
        lowered = body_text.lower()
        return "quota" in lowered or "limit" in lowered
    return False


def _song_id_from_headers(headers: dict) -> str:
    """`song_id` живёт в заголовке ответа; имя заголовка не документировано жёстко —
    берём первый, чьё имя (без учёта регистра) содержит «song-id»/«song_id»."""
    for name, value in (headers or {}).items():
        lowered = str(name).lower()
        if "song-id" in lowered or "song_id" in lowered:
            return str(value or "").strip()
    return ""


def _urllib_transport(url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, dict, bytes]:
    """Настоящая сеть. Сбои делит на «запрос не ушёл» и «мог дойти» (`_network_error`):
    от этого зависит, можно ли повторить без второй оплаты."""
    import urllib.request

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        resp_headers = dict(exc.headers.items()) if exc.headers else {}
        try:
            text = exc.read()
        except Exception:  # noqa: BLE001 — код ответа известен, тело ошибки не обязательно
            text = b""
        return exc.code, resp_headers, text
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise _network_error(exc) from exc


def compose_music(
    prompt: str,
    seconds: int,
    *,
    api_key: str | None = None,
    transport: Transport | None = None,
    timeout: float = 600,
) -> tuple[bytes, str]:
    """Запрашивает трек у ElevenLabs. Возвращает (mp3-байты, song_id).

    `api_key=None` — берётся `settings.elevenlabs_api_key`; параметр существует ради тестов
    и вызова с ключом, взятым не из общих настроек.
    """
    from app.config import settings

    key = api_key if api_key is not None else settings.elevenlabs_api_key
    if not key:
        raise ElevenLabsError("ELEVENLABS_API_KEY не задан")

    send = transport or _urllib_transport
    body = json.dumps({
        "prompt": prompt,
        "music_length_ms": int(seconds) * 1000,
        "force_instrumental": True,
        "model_id": MODEL_ID,
    }).encode("utf-8")
    url = f"{MUSIC_URL}?output_format={OUTPUT_FORMAT}"
    headers = {"xi-api-key": key, "Content-Type": "application/json"}

    try:
        status, resp_headers, resp_body = send(url, headers, body, timeout)
    except ElevenLabsError:
        raise
    except Exception as exc:  # noqa: BLE001 — транспорт мог кинуть что угодно сетевое
        raise _network_error(exc) from exc

    if status >= 500:
        raise ElevenLabsUnavailable(f"ElevenLabs недоступен: http {status}", status=status, sent=True)
    if status >= 400:
        text = (resp_body or b"").decode("utf-8", errors="replace")[:200]
        if _is_quota_status(status, text):
            raise QuotaExhausted(f"квота исчерпана: http {status}", status=status)
        if status in (401, 403):
            raise BadKey(f"ключ отклонён: http {status}", status=status)
        raise ElevenLabsError(f"http {status}: {text}", status=status)
    if not resp_body:
        raise ElevenLabsError(f"пустой ответ: http {status}", status=status)

    return resp_body, _song_id_from_headers(resp_headers)
