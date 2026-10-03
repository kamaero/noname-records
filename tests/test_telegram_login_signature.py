"""Подпись входа через Telegram: отсутствующие поля в неё не входят (02.10).

Страница входа шлёт все семь полей формы, и отсутствующие у человека (фамилия, @username,
фото) приходят пустыми строками. Telegram их в подпись не включает — сервер включал как
«last_name=», и у всех без фамилии или ника подпись не сходилась никогда.
"""
import hashlib
import hmac
import time

from app.auth import verify_telegram_payload
from app.config import settings


def _signed(fields: dict, token: str) -> dict:
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hashlib.sha256(token.encode()).digest()
    return {**fields, "hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()}


def test_a_person_without_last_name_username_and_photo_signs_in(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "123:abc")
    telegram_sent = {"id": "900000113", "first_name": "Moonlight7", "auth_date": str(int(time.time()))}
    form = {**_signed(telegram_sent, "123:abc"), "last_name": "", "username": "", "photo_url": ""}
    assert verify_telegram_payload(form) == (True, "")


def test_a_forged_field_still_fails(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "123:abc")
    form = _signed({"id": "1", "first_name": "A", "auth_date": str(int(time.time()))}, "123:abc")
    form["first_name"] = "B"
    assert verify_telegram_payload(form)[0] is False
