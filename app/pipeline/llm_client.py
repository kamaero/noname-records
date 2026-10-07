"""
LLM HTTP client layer.
Handles provider resolution, request retries, and response normalisation.
All LLM network I/O goes through this module.
"""
from __future__ import annotations

import logging

import json
import threading
import time
from typing import Any

import requests

from app.config import env_value, settings

logger = logging.getLogger(__name__)


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """A copy of `schema` that satisfies OpenAI-style `strict: true`.

    Strict mode has two rules our schemas did not follow: every property must be
    listed in `required`, and objects must forbid extra keys. DeepSeek and Anthropic
    ignore both; a provider that enforces them rejects the request outright, which
    looks exactly like a model that cannot answer. A property that was optional is
    added to `required` and widened to accept null, so «nothing to say here» is still
    sayable.
    """
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if isinstance(value, dict):
            out[key] = strict_json_schema(value) if key != "properties" else {
                name: strict_json_schema(prop) for name, prop in value.items()
            }
        elif isinstance(value, list):
            out[key] = [strict_json_schema(item) if isinstance(item, dict) else item for item in value]
        else:
            out[key] = value
    if out.get("type") == "object" and isinstance(out.get("properties"), dict):
        names = list(out["properties"])
        required = [name for name in out.get("required") or []]
        optional = [name for name in names if name not in required]
        for name in optional:
            prop = out["properties"][name]
            kind = prop.get("type")
            if isinstance(kind, str) and kind != "null":
                prop["type"] = [kind, "null"]
        out["required"] = names
        out.setdefault("additionalProperties", False)
    return out


def _safe(message: str) -> str:
    """Текст ошибки — без ключей: провайдер мог повторить присланный ключ в ответе."""
    from app.services.provider_keys import redact

    return redact(message)


def _resolve_provider(provider_name: str) -> tuple[str, str, str]:
    from app.services.provider_keys import provider_key

    providers = {
        "openai": ("openai", settings.openai_base_url, "openai"),
        "claude": ("claude", settings.claude_base_url, "anthropic"),
        # deepseek-v4-pro via the Anthropic Messages endpoint (unified path + tool-use).
        "deepseek": ("deepseek", settings.deepseek_anthropic_base_url, "anthropic"),
        # Legacy OpenAI-compatible DeepSeek route, kept for rollback / non-tool calls.
        "deepseek-openai": ("deepseek", settings.deepseek_base_url, "openai"),
        "z.ai": ("zai", settings.zai_base_url, "openai"),
        "zai": ("zai", settings.zai_base_url, "openai"),
        "openrouter": ("openrouter", settings.openrouter_base_url, "openai"),
        "routerai.ru": ("routerai", settings.routerai_base_url, "openai"),
        "routerai": ("routerai", settings.routerai_base_url, "openai"),
    }
    if provider_name not in providers:
        raise RuntimeError(f"Неподдерживаемый провайдер: {provider_name}")
    key_name, base_url, mode = providers[provider_name]
    return provider_key(key_name), base_url.rstrip("/"), mode


def call_chat(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    mode: str,
    force_json: bool = False,
    json_schema: dict[str, Any] | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict:
    """Single dispatch point for an LLM chat turn.

    ``extra_body`` is merged into the request payload as-is (e.g. a provider's
    ``thinking`` switch); callers that don't pass it get the unchanged behaviour.

    Routes to the Anthropic Messages path (Claude + DeepSeek's /anthropic) or the
    OpenAI-compatible path based on ``mode``, passing the structured-output args
    to BOTH. Call sites should use this instead of branching, so strict JSON is
    never silently dropped on one path.
    """
    if mode == "anthropic":
        out = _call_anthropic_messages(
            base_url, api_key, model, system_prompt, user_prompt,
            force_json=force_json, json_schema=json_schema, extra_body=extra_body,
        )
    else:
        out = _call_openai_compatible_chat(
            base_url, api_key, model, system_prompt, user_prompt,
            force_json=force_json, json_schema=json_schema, extra_body=extra_body,
        )
    return out


def _record_spend(url: str, model: str, prompt_tokens, completion_tokens) -> None:
    """Строка журнала трат — сразу, как провайдер ответил, до разбора ответа: обрезанный или
    кривой ответ уже оплачен. Нет хотя бы одного из счётчиков — цена неизвестна, а не ноль."""
    from app.services import spend

    known = isinstance(prompt_tokens, int) and isinstance(completion_tokens, int)
    spend.record_call(spend.provider_from_url(url), str(model or ""), unit="tokens",
                      input_units=int(prompt_tokens or 0) if known else 0,
                      output_units=int(completion_tokens or 0) if known else 0, estimated=not known)


def _transport_error_label(exc: Exception) -> str:
    text = str(exc or "").strip()
    lower = text.lower()
    if "response ended prematurely" in lower:
        return "response_ended_prematurely"
    if "incomplete read" in lower:
        return "incomplete_read"
    if "chunked" in lower:
        return "chunked_transfer_error"
    if "connection aborted" in lower:
        return "connection_aborted"
    return type(exc).__name__


def _is_retriable_transport_error(exc: Exception) -> bool:
    retriable_types = (
        requests.exceptions.ReadTimeout,
        requests.exceptions.ConnectionError,
        requests.exceptions.ChunkedEncodingError,
        requests.exceptions.ContentDecodingError,
    )
    if isinstance(exc, retriable_types):
        return True
    text = str(exc or "").lower()
    return any(
        marker in text
        for marker in (
            "response ended prematurely",
            "incomplete read",
            "connection aborted",
            "chunked",
        )
    )


def _retry_count() -> int:
    return max(1, int(settings.llm_transport_retries or 1))


def _read_timeout() -> int:
    return max(30, int(settings.llm_request_timeout_seconds or 180))


def _idle_content_deadline() -> int:
    return max(60, int(settings.llm_idle_content_deadline_seconds or 300))


def _stream_wall_deadline() -> int:
    return max(120, int(settings.llm_stream_wall_deadline_seconds or 1200))


def _force_close_response(resp) -> None:
    """Close the underlying socket so a read blocked in poll() raises at once."""
    for closer in (getattr(getattr(resp, "raw", None), "close", None), getattr(resp, "close", None)):
        if closer:
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass


def _consume_with_wall_deadline(resp, parse, wall_seconds: int, *, timer_cls=threading.Timer):
    """Run ``parse(resp)`` under a hard wall-clock; force-close + retriable error if exceeded.

    Guards the case the per-chunk read timeout and the line-level idle deadline both
    miss: a socket read stuck in poll() that never yields a line.
    """
    fired = {"v": False}

    def _kill() -> None:
        fired["v"] = True
        _force_close_response(resp)

    timer = timer_cls(wall_seconds, _kill)
    timer.daemon = True
    timer.start()
    try:
        return parse(resp)
    except Exception as exc:  # noqa: BLE001
        if fired["v"]:
            raise requests.exceptions.ReadTimeout(
                f"LLM stream wall-clock deadline {wall_seconds}s exceeded (forced close)"
            ) from exc
        raise
    finally:
        timer.cancel()


def _rate_limit_retries() -> int:
    # 429s are worth waiting out (org ITPM ceiling), so be more patient than transport retries.
    return max(_retry_count(), 6)


# Statuses that mean "the provider is momentarily unable", not "your request is wrong".
# 529 is Anthropic's "Overloaded"; the DeepSeek endpoint speaks that dialect too.
# 500/501 are deliberately absent: an unknown 5xx may be a deterministic rejection,
# and retrying it burns the budget without any chance of succeeding.
_RETRIABLE_HTTP_STATUSES = frozenset({502, 503, 504, 529})


def _is_retriable_http_status(status_code: int) -> bool:
    """Whether an HTTP status is worth another attempt.

    429 is excluded on purpose — it has its own branch with Retry-After parsing and
    a more patient budget, and counting it here too would halve that patience.
    """
    return int(status_code) in _RETRIABLE_HTTP_STATUSES


def _overload_retries() -> int:
    # A 60-chapter book is hundreds of calls; a provider blip is a certainty. Chapter
    # 15 of «Крылья полумрака» lost its FINAL to a single 503 that was never retried.
    return max(_retry_count(), 5)


def _parse_retry_after(resp, default: int = 20) -> int:
    """Seconds to wait on a 429, honouring the Retry-After header when present."""
    raw = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
    try:
        return max(1, min(120, int(float(raw))))
    except (TypeError, ValueError):
        return default


def _post_with_retry(url: str, headers: dict[str, str], payload: dict[str, Any]) -> requests.Response:
    last_exc: Exception | None = None
    retries = _retry_count()
    for attempt in range(retries):
        try:
            return requests.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=_read_timeout(),
            )
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if not _is_retriable_transport_error(exc):
                raise RuntimeError(_safe(f"LLM transport non-retriable error [{_transport_error_label(exc)}]: {exc}")) from exc
            if attempt >= retries - 1:
                raise RuntimeError(_safe(f"LLM transport error after {retries} attempts [{_transport_error_label(exc)}]: {exc}")) from exc
            time.sleep(2 + attempt * 3)
    raise RuntimeError(_safe(f"LLM transport error [{_transport_error_label(last_exc or RuntimeError('unknown'))}]: {last_exc}"))


def _post_stream_with_retry(url: str, headers: dict[str, str], payload: dict[str, Any], parse) -> Any:
    """POST with ``stream=True`` and let ``parse`` consume the SSE body.

    Why streaming: the read timeout then applies to the gap *between* chunks, not
    to the whole generation. Non-streaming calls had to receive the entire (up to
    16k-token) ``final``-stage response inside one flat 180s window and timed out
    on long chapters. Retries cover mid-stream transport errors.
    """
    retries = _retry_count()
    rate_retries = _rate_limit_retries()
    overload_retries = _overload_retries()
    read_to = _read_timeout()
    transport_attempt = 0
    rate_attempt = 0
    overload_attempt = 0
    while True:
        started = False
        try:
            with requests.post(
                url,
                headers=headers,
                data=json.dumps(payload),
                stream=True,
                timeout=(min(30, read_to), read_to),
            ) as resp:
                if resp.status_code == 429:
                    rate_attempt += 1
                    if rate_attempt > rate_retries:
                        raise RuntimeError(_safe(f"LLM HTTP 429 after {rate_attempt} attempts: {(resp.text or '')[:160]}"))
                    time.sleep(_parse_retry_after(resp))
                    continue
                if _is_retriable_http_status(resp.status_code):
                    overload_attempt += 1
                    if overload_attempt > overload_retries:
                        raise RuntimeError(
                            f"LLM HTTP {resp.status_code} after {overload_attempt} attempts: "
                            f"{(resp.text or '')[:160]}"
                        )
                    # Exponential-ish backoff with a ceiling: an overloaded provider
                    # needs seconds, not milliseconds, and hammering it makes it worse.
                    time.sleep(min(30, 3 * (2 ** (overload_attempt - 1))))
                    continue
                if resp.status_code >= 400:
                    raise RuntimeError(_safe(f"LLM HTTP {resp.status_code}: {(resp.text or '')[:180]}"))
                started = True
                return _consume_with_wall_deadline(resp, parse, _stream_wall_deadline())
        except requests.exceptions.RequestException as exc:
            if started:
                # Генерация шла и оборвалась: провайдер мог её посчитать. Строка «цена
                # неизвестна» честнее пропуска — повтор ниже оплатится отдельно.
                _record_spend(url, payload.get("model"), None, None)
            if not _is_retriable_transport_error(exc):
                raise RuntimeError(_safe(f"LLM transport non-retriable error [{_transport_error_label(exc)}]: {exc}")) from exc
            transport_attempt += 1
            if transport_attempt >= retries:
                raise RuntimeError(_safe(f"LLM transport error after {retries} attempts [{_transport_error_label(exc)}]: {exc}")) from exc
            time.sleep(2 + (transport_attempt - 1) * 3)


def _parse_anthropic_stream(resp, *, idle_deadline: int | None = None, clock=time.monotonic) -> dict[str, Any]:
    """Accumulate text + tool_use JSON + usage from an Anthropic SSE stream.

    ``idle_deadline``: max seconds without a real protocol event before aborting
    with a retriable ReadTimeout. Keepalive pings (non-``data:`` lines) do not
    count as progress, so a stalled-but-pinging generation is caught instead of
    hanging forever.
    """
    deadline = idle_deadline if idle_deadline is not None else _idle_content_deadline()
    last_progress = clock()
    text_parts: list[str] = []
    tool_json: dict[int, str] = {}
    usage = {"input_tokens": 0, "output_tokens": 0}
    stop_reason: str | None = None
    current_event: str | None = None
    for raw in resp.iter_lines(decode_unicode=True):
        if clock() - last_progress > deadline:
            raise requests.exceptions.ReadTimeout(
                f"LLM stream idle: no content for >{deadline}s (stalled generation)"
            )
        if not raw:
            continue
        line = raw if isinstance(raw, str) else raw.decode("utf-8")
        line = line.strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except ValueError:
            continue
        last_progress = clock()  # a real protocol event = the stream is alive
        etype = obj.get("type") or current_event
        if etype == "message_start":
            u = (obj.get("message") or {}).get("usage") or {}
            # Anthropic-style usage reports cached prompt tokens apart from
            # input_tokens; DeepSeek's cache makes the bare number tiny (906 for
            # a whole chapter). What was sent is the sum, and that is what we log.
            usage["input_tokens"] = (
                int(u.get("input_tokens") or 0)
                + int(u.get("cache_read_input_tokens") or 0)
                + int(u.get("cache_creation_input_tokens") or 0)
            )
            usage["output_tokens"] = int(u.get("output_tokens") or usage["output_tokens"])
        elif etype == "content_block_start":
            cb = obj.get("content_block") or {}
            if cb.get("type") == "tool_use":
                tool_json[int(obj.get("index", 0))] = ""
        elif etype == "content_block_delta":
            idx = int(obj.get("index", 0))
            delta = obj.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                text_parts.append(delta.get("text", ""))
            elif dtype == "input_json_delta":
                tool_json[idx] = tool_json.get(idx, "") + (delta.get("partial_json") or "")
        elif etype == "message_delta":
            u = obj.get("usage") or {}
            if u.get("output_tokens") is not None:
                usage["output_tokens"] = int(u.get("output_tokens"))
            sr = (obj.get("delta") or {}).get("stop_reason")
            if sr:
                stop_reason = sr
        elif etype == "error":
            err = obj.get("error") or {}
            raise RuntimeError(_safe(f"LLM stream error [{err.get('type')}]: {err.get('message')}"))
    tool_inputs: list[Any] = []
    for idx in sorted(tool_json):
        raw_json = tool_json[idx]
        if raw_json.strip():
            try:
                tool_inputs.append(json.loads(raw_json))
            except ValueError:
                logger.warning("Ответ инструмента модели — не JSON (%s знаков), пропущен", len(raw_json))
    return {"text": "".join(text_parts), "tool_inputs": tool_inputs, "usage": usage, "stop_reason": stop_reason}


def _call_openai_compatible_chat(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    force_json: bool = False,
    json_schema: dict[str, Any] | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict:
    url = f"{base_url}/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.0,
        "max_tokens": max(1024, int(settings.llm_max_tokens or 12000)),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if json_schema:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "structured_output",
                "schema": strict_json_schema(json_schema),
                "strict": True,
            }
        }
    elif force_json:
        payload["response_format"] = {"type": "json_object"}
    if extra_body:
        payload.update(extra_body)
    resp = _post_with_retry(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        payload=payload,
    )
    if (force_json or json_schema) and resp.status_code >= 400 and "response_format" in (resp.text or ""):
        payload.pop("response_format", None)
        resp = _post_with_retry(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            payload=payload,
        )
    if resp.status_code >= 400:
        raise RuntimeError(_safe(f"LLM HTTP {resp.status_code}: {resp.text[:180]}"))
    data = resp.json()
    usage = data.get("usage") or {}
    _record_spend(base_url, model, usage.get("prompt_tokens"), usage.get("completion_tokens"))
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    if not total_tokens:
        estimated_total = max(1, int((len(system_prompt) + len(user_prompt) + len(json.dumps(data, ensure_ascii=False))) / 4))
        total_tokens = estimated_total
        prompt_tokens = max(1, int((len(system_prompt) + len(user_prompt)) / 4))
        completion_tokens = max(0, total_tokens - prompt_tokens)
        estimated = True
    else:
        estimated = False
    return {
        "content": data["choices"][0]["message"]["content"].strip(),
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated": estimated,
        },
    }


def _extract_json_object(text: str) -> str:
    """Best-effort recovery of a JSON object from free-form model text.

    Used only as a fallback when a model answers with prose instead of calling
    the structured-output tool (strips ```json fences, returns first {...}).
    """
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)
        cleaned = cleaned[1] if len(cleaned) > 1 else (text or "")
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    return cleaned[start : end + 1] if start != -1 and end > start else cleaned.strip()


def _call_anthropic_messages(
    base_url: str,
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    force_json: bool = False,
    json_schema: dict[str, Any] | None = None,
    extra_body: dict[str, Any] | None = None,
) -> dict:
    """Anthropic Messages call, used for BOTH Claude and DeepSeek's /anthropic endpoint.

    Strict JSON is requested via tool-use (a single `emit_result` tool with the
    given schema). We use ``tool_choice: auto`` rather than forcing the tool,
    because thinking models reject a forced tool_choice — and we want to keep
    thinking ON for accuracy-critical stages. A robust fallback parses JSON from
    text if the model declines to call the tool. Verified against deepseek-v4-pro.
    """
    url = f"{base_url}/messages"
    want_json = bool(json_schema) or bool(force_json)
    # final/char_extraction emit a full chapter/book as one JSON object; 16000 was
    # too small and truncated it (-> invalid tool JSON -> "не вернула JSON-объект").
    # Use the configured budget, capped at the model's output ceiling.
    max_tokens = max(4096, min(int(settings.llm_max_tokens or 16000), 64000))
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
        # Stream so the read timeout applies per-chunk, not to the whole (long)
        # generation — fixes final-stage ReadTimeouts on big chapters.
        "stream": True,
    }
    if json_schema:
        payload["tools"] = [
            {
                "name": "emit_result",
                "description": "Return the result strictly matching the provided JSON schema.",
                "input_schema": json_schema,
            }
        ]
        payload["tool_choice"] = {"type": "auto"}
    if extra_body:
        payload.update(extra_body)
    result = _post_stream_with_retry(
        url,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        payload=payload,
        parse=_parse_anthropic_stream,
    )
    _record_spend(base_url, model, (result.get("usage") or {}).get("input_tokens"),
                  (result.get("usage") or {}).get("output_tokens"))
    tool_inputs = result["tool_inputs"]
    # Truncation guard: a max_tokens cut-off leaves the tool JSON incomplete
    # (json.loads fails -> tool_inputs empty). Surface it clearly instead of
    # letting it become empty content and a confusing downstream parse error.
    if want_json and not tool_inputs and result.get("stop_reason") == "max_tokens":
        out_tokens = (result.get("usage") or {}).get("output_tokens")
        raise RuntimeError(
            f"LLM output truncated at max_tokens (output_tokens={out_tokens}, cap={max_tokens}); "
            "raise LLM_MAX_TOKENS or reduce FINAL_PART_CHARS/part size"
        )
    if json_schema and tool_inputs:
        content = json.dumps(tool_inputs[0], ensure_ascii=False)
    else:
        text = (result["text"] or "").strip()
        content = _extract_json_object(text) if want_json else text
    usage = result["usage"]
    prompt_tokens = int(usage.get("input_tokens") or 0)
    completion_tokens = int(usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (prompt_tokens + completion_tokens))
    if not total_tokens:
        answer = (result.get("text") or "") + json.dumps(tool_inputs, ensure_ascii=False)
        total_tokens = max(1, int((len(system_prompt) + len(user_prompt) + len(answer)) / 4))
        prompt_tokens = max(1, int((len(system_prompt) + len(user_prompt)) / 4))
        completion_tokens = max(0, total_tokens - prompt_tokens)
        estimated = True
    else:
        estimated = False
    return {
        "content": content,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "estimated": estimated,
        },
    }
