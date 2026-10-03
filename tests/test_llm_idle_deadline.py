"""A stalled-but-pinging SSE stream must abort on the idle-content deadline.

The per-chunk read timeout resets on every line, so keepalive pings (which carry
no content) let a stuck generation hang forever. _parse_anthropic_stream must
track time since the last real protocol event and raise a retriable ReadTimeout
when no content arrives for longer than the deadline.
"""

import requests
import pytest

from app.pipeline.llm_client import (
    _parse_anthropic_stream,
    _is_retriable_transport_error,
    _consume_with_wall_deadline,
)


class _ImmediateTimer:
    """Fires synchronously on start() — deterministic wall-clock test."""

    def __init__(self, secs, fn):
        self._fn = fn
        self.daemon = False

    def start(self):
        self._fn()

    def cancel(self):
        pass


class _NoopTimer:
    def __init__(self, secs, fn):
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        pass


class _Sock:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_wall_deadline_forces_close_and_raises_retriable():
    sock = _Sock()
    resp = type("R", (), {"raw": sock, "close": lambda self: None})()

    def parse(r):
        # timer already fired before parse runs → socket closed → read would raise
        if sock.closed:
            raise ConnectionError("read of closed socket")
        return {"text": "unreached"}

    with pytest.raises(requests.exceptions.ReadTimeout) as ei:
        _consume_with_wall_deadline(resp, parse, 1, timer_cls=_ImmediateTimer)
    assert sock.closed is True
    assert _is_retriable_transport_error(ei.value)


def test_wall_deadline_returns_when_parse_completes():
    sock = _Sock()
    resp = type("R", (), {"raw": sock, "close": lambda self: None})()
    out = _consume_with_wall_deadline(resp, lambda r: {"text": "ok"}, 1, timer_cls=_NoopTimer)
    assert out == {"text": "ok"}
    assert sock.closed is False


class _Resp:
    """Fake streaming response; advances the shared clock before each yielded line."""

    def __init__(self, lines, clock_box, step):
        self._lines = lines
        self._clock = clock_box
        self._step = step

    def iter_lines(self, decode_unicode=True):
        for ln in self._lines:
            self._clock[0] += self._step
            yield ln


def test_idle_pings_raise_retriable_readtimeout():
    clock = [0.0]
    lines = (
        ['event: message_start',
         'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}']
        + [': ping'] * 8           # keepalive comments, no content
    )
    resp = _Resp(lines, clock, step=150.0)   # 150s between lines
    with pytest.raises(requests.exceptions.ReadTimeout) as ei:
        _parse_anthropic_stream(resp, idle_deadline=300, clock=lambda: clock[0])
    assert _is_retriable_transport_error(ei.value)   # must be retried by the wrapper


def test_continuous_content_completes():
    clock = [0.0]
    lines = [
        'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Привет"}}',
        'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":" мир"}}',
        'data: [DONE]',
    ]
    resp = _Resp(lines, clock, step=150.0)   # gaps < deadline → never trips
    out = _parse_anthropic_stream(resp, idle_deadline=300, clock=lambda: clock[0])
    assert out["text"] == "Привет мир"
