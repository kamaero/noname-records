"""A provider that is momentarily overloaded must be retried, not fatal.

Re-running FINAL for chapter 15 of «Крылья полумрака» failed twice in three
seconds: DeepSeek's Anthropic-compatible endpoint answered HTTP 503 "Server
Overloaded". The client retried 429 and transport errors but treated every other
4xx/5xx as final, so one blink of the provider killed the chapter's FINAL and the
pipeline degraded it to the draft.

For a 60-chapter book that is hundreds of model calls: transient 5xx is a
certainty, not an edge case. A bad request must still fail fast — retrying a 400
just burns time and money.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.pipeline.llm_client import _is_retriable_http_status


def test_server_overloaded_is_retriable():
    # The exact status DeepSeek returned while overloaded.
    assert _is_retriable_http_status(503) is True


def test_the_other_transient_gateway_statuses_are_retriable():
    assert _is_retriable_http_status(502) is True
    assert _is_retriable_http_status(504) is True
    # Anthropic-style "Overloaded" — the endpoint speaks that dialect.
    assert _is_retriable_http_status(529) is True


def test_client_errors_are_never_retried():
    # Retrying these cannot help and costs time on every chapter.
    for code in (400, 401, 403, 404, 422):
        assert _is_retriable_http_status(code) is False


def test_rate_limit_is_not_handled_here():
    # 429 has its own branch with Retry-After parsing; it must not be double-counted
    # against the generic 5xx budget.
    assert _is_retriable_http_status(429) is False


def test_success_is_not_a_retry():
    for code in (200, 201, 299):
        assert _is_retriable_http_status(code) is False


def test_an_unexpected_5xx_is_not_retried_blindly():
    # Only the statuses that actually mean "try again" — an unknown 5xx may be a
    # deterministic server-side rejection, and hammering it wastes the budget.
    assert _is_retriable_http_status(500) is False
    assert _is_retriable_http_status(501) is False


# The predicate alone proves nothing about behaviour — the pre-push hook in this same
# session read the right value from the wrong field and stayed silent. These exercise
# the request loop itself.

from app.pipeline import llm_client as llm  # noqa: E402
from tests.test_llm_client_streaming import FakeStreamResponse, TEXT_SSE  # noqa: E402


def _sequence(monkeypatch, responses):
    calls = {"n": 0}

    def fake_post(url, headers=None, data=None, timeout=None, stream=False, **kw):
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(llm.requests, "post", fake_post)
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    return calls


def test_a_503_is_retried_and_the_call_then_succeeds(monkeypatch):
    """Exactly what killed chapter 15's FINAL: one 503, then the provider is fine."""
    calls = _sequence(monkeypatch, [
        FakeStreamResponse([], status=503, text='{"error":{"message":"Server Overloaded"}}'),
        FakeStreamResponse(TEXT_SSE),
    ])
    out = llm._call_anthropic_messages("https://api.deepseek.com/anthropic/v1", "k", "deepseek-v4-pro", "sys", "hi")
    assert out["content"] == "Привет, мир"
    assert calls["n"] == 2


def test_a_persistent_503_eventually_gives_up_with_the_attempt_count(monkeypatch):
    calls = _sequence(monkeypatch, [
        FakeStreamResponse([], status=503, text='{"error":{"message":"Server Overloaded"}}'),
    ])
    try:
        llm._call_anthropic_messages("https://api.deepseek.com/anthropic/v1", "k", "deepseek-v4-pro", "sys", "hi")
    except RuntimeError as exc:
        assert "503" in str(exc)
        assert "attempts" in str(exc)
    else:
        raise AssertionError("expected RuntimeError after exhausting overload retries")
    assert calls["n"] > 1, "a persistent 503 must have been retried, not failed on the first try"


def test_a_400_still_fails_on_the_first_attempt(monkeypatch):
    calls = _sequence(monkeypatch, [
        FakeStreamResponse([], status=400, text='{"error":{"message":"bad request"}}'),
    ])
    try:
        llm._call_anthropic_messages("https://api.deepseek.com/anthropic/v1", "k", "deepseek-v4-pro", "sys", "hi")
    except RuntimeError as exc:
        assert "400" in str(exc)
    else:
        raise AssertionError("expected RuntimeError on HTTP 400")
    assert calls["n"] == 1, "a bad request must not be retried"
