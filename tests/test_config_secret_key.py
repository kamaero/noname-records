from __future__ import annotations

from app.config import MIN_SECRET_KEY_LENGTH, classify_secret_key


def test_placeholder_and_empty_secret_keys_flagged():
    for value in ("", "change-me", "change-this-secret", "  change-this-secret  ", "secret"):
        assert classify_secret_key(value) == "placeholder"


def test_short_non_placeholder_key_flagged_short():
    assert classify_secret_key("x" * (MIN_SECRET_KEY_LENGTH - 1)) == "short"


def test_strong_random_key_ok():
    assert classify_secret_key("a" * MIN_SECRET_KEY_LENGTH) == "ok"
    assert classify_secret_key("k7Qz" * 12) == "ok"
