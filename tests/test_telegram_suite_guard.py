"""Safety net: the test suite must never emit a live Telegram message.

A real outbound message once leaked from test_pipeline_repair because its failure
path runs notify_book_status_change -> app.services.telegram.send_telegram_message,
which the test patched only on the script_pipeline module. The conftest guard
disables Telegram at the single chokepoint (settings.telegram_notify_enabled), so
*any* call path returns 0 without touching the network.
"""

import app.services.telegram as tg
from app.config import settings


def test_notify_disabled_for_whole_suite():
    assert settings.telegram_notify_enabled is False


def test_send_is_inert_even_with_token(monkeypatch):
    # Simulate a fully-configured prod env; the flag must still short-circuit.
    monkeypatch.setattr(settings, "telegram_bot_token", "1234:REALTOKEN", raising=False)
    monkeypatch.setattr(settings, "owner_telegram_id", "900000100", raising=False)

    def _explode(*_a, **_kw):  # any network attempt is a test failure
        raise AssertionError("send_telegram_message attempted a live request during tests")

    monkeypatch.setattr(tg.requests, "post", _explode)
    assert tg.send_telegram_message(None, "should not be sent") == 0
