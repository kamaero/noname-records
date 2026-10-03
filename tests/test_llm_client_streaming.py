"""Streaming Anthropic Messages path.

Root cause of the production `final`-stage ReadTimeouts: the Anthropic call was
non-streaming, so the whole generation had to arrive inside one flat read
timeout. These tests pin the streaming behaviour: stream=True is sent, text and
tool_use deltas are accumulated from the SSE stream, and usage is parsed.
"""
from __future__ import annotations

import json

import app.pipeline.llm_client as llm


class FakeStreamResponse:
    """Minimal stand-in for a requests streaming Response."""

    def __init__(self, lines, status=200, text="", headers=None):
        self._lines = lines
        self.status_code = status
        self.text = text
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self, decode_unicode=False, **_):
        for line in self._lines:
            yield line if decode_unicode else line.encode("utf-8")

    def json(self):
        return {}


def _install(monkeypatch, lines, status=200, text=""):
    captured = {}

    def fake_post(url, headers=None, data=None, timeout=None, stream=False, **kw):
        captured["url"] = url
        captured["stream"] = stream
        captured["timeout"] = timeout
        captured["payload"] = json.loads(data) if data else {}
        return FakeStreamResponse(lines, status=status, text=text)

    monkeypatch.setattr(llm.requests, "post", fake_post)
    return captured


TEXT_SSE = [
    "event: message_start",
    'data: {"type":"message_start","message":{"usage":{"input_tokens":1200,"output_tokens":1}}}',
    "",
    "event: content_block_start",
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
    "",
    "event: content_block_delta",
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Привет, "}}',
    "",
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"мир"}}',
    "",
    "event: message_delta",
    'data: {"type":"message_delta","delta":{},"usage":{"output_tokens":42}}',
    "",
    "event: message_stop",
    'data: {"type":"message_stop"}',
]

TOOL_SSE = [
    'data: {"type":"message_start","message":{"usage":{"input_tokens":50,"output_tokens":1}}}',
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","name":"emit_result","input":{}}}',
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"ok\\": "}}',
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"true}"}}',
    'data: {"type":"message_delta","delta":{},"usage":{"output_tokens":7}}',
    'data: {"type":"message_stop"}',
]


def test_anthropic_streams_and_accumulates_text(monkeypatch):
    cap = _install(monkeypatch, TEXT_SSE)
    out = llm._call_anthropic_messages("https://api.anthropic.com/v1", "k", "claude-sonnet-4-6", "sys", "hi")
    assert out["content"] == "Привет, мир"
    assert out["usage"]["prompt_tokens"] == 1200
    assert out["usage"]["completion_tokens"] == 42
    # The fix: request must be streaming so the read timeout is per-chunk.
    assert cap["stream"] is True
    assert cap["payload"].get("stream") is True
    assert cap["url"].endswith("/messages")


def test_anthropic_streams_tool_use_json(monkeypatch):
    cap = _install(monkeypatch, TOOL_SSE)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    out = llm._call_anthropic_messages(
        "https://api.anthropic.com/v1", "k", "claude-sonnet-4-6", "sys", "hi", json_schema=schema
    )
    assert json.loads(out["content"]) == {"ok": True}
    assert cap["payload"]["tools"][0]["name"] == "emit_result"
    assert cap["stream"] is True


TRUNCATED_TOOL_SSE = [
    'data: {"type":"message_start","message":{"usage":{"input_tokens":100,"output_tokens":1}}}',
    'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","name":"emit_result","input":{}}}',
    # partial_json is cut off mid-string (no closing) — what max_tokens truncation produces
    'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"fountain\\": \\"line1"}}',
    'data: {"type":"message_delta","delta":{"stop_reason":"max_tokens"},"usage":{"output_tokens":150}}',
    'data: {"type":"message_stop"}',
]


def test_anthropic_truncated_tool_raises_clear_error(monkeypatch):
    """A max_tokens-truncated tool JSON must surface a clear truncation error,
    not silently become empty content -> downstream 'не вернула JSON-объект'."""
    _install(monkeypatch, TRUNCATED_TOOL_SSE)
    schema = {"type": "object", "properties": {"fountain": {"type": "string"}}}
    try:
        llm._call_anthropic_messages(
            "https://api.anthropic.com/v1", "k", "claude-sonnet-4-6", "sys", "hi", json_schema=schema
        )
    except RuntimeError as exc:
        assert "max_tokens" in str(exc).lower() or "truncat" in str(exc).lower()
    else:
        raise AssertionError("expected a truncation RuntimeError")


def test_anthropic_429_retries_then_succeeds(monkeypatch):
    """A 429 must be waited out (Retry-After) and retried, not failed immediately."""
    seq = [
        FakeStreamResponse([], status=429, text='{"error":{"type":"rate_limit_error"}}', headers={"retry-after": "0"}),
        FakeStreamResponse(TEXT_SSE),
    ]
    calls = {"n": 0}

    def fake_post(url, headers=None, data=None, timeout=None, stream=False, **kw):
        r = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    out = llm._call_anthropic_messages("https://api.anthropic.com/v1", "k", "claude-sonnet-4-6", "sys", "hi")
    assert out["content"] == "Привет, мир"
    assert calls["n"] == 2  # 429 once, then success


def test_anthropic_http_error_raises(monkeypatch):
    # Sleep is mocked out: a 429 waits Retry-After (default 20s) on each of six
    # attempts, so without this the test spends two real minutes asleep — 120 of the
    # suite's 168 seconds. It asserts the give-up behaviour, not the wall clock.
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    _install(monkeypatch, [], status=429, text='{"error":{"type":"rate_limit"}}')
    try:
        llm._call_anthropic_messages("https://api.anthropic.com/v1", "k", "m", "sys", "hi")
    except RuntimeError as exc:
        assert "429" in str(exc)
    else:
        raise AssertionError("expected RuntimeError on HTTP 429")
