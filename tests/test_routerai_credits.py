"""Баланс RouterAI никогда не роняет вызывающий код — ни сетью, ни кривым ответом."""
from app.services.routerai_credits import read_credits


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_non_numeric_credits_value_returns_none_not_raise(monkeypatch):
    messages: list[str] = []

    def fake_get(*args, **kwargs):
        return _FakeResponse(200, {"data": {"credits": "n/a"}})

    monkeypatch.setattr("requests.get", fake_get)
    assert read_credits(log=messages.append) is None
    assert messages and "баланс не прочитан" in messages[0]


def test_network_exception_returns_none(monkeypatch):
    def fake_get(*args, **kwargs):
        raise ConnectionError("boom")

    monkeypatch.setattr("requests.get", fake_get)
    assert read_credits() is None
