"""Пароли без passlib: тот же формат `$pbkdf2-sha256$...`, что уже лежит в базе.

passlib не обновлялся с 2020 года и тянет за собой `crypt`, которого в Python 3.13 нет.
Хеши ниже выпущены самим passlib 1.7.4 (`CryptContext(schemes=["pbkdf2_sha256"])`) —
если новый модуль их не принимает, после выкатки не войдёт никто.
"""
import pytest

from app.services.passwords import hash_password, verify_password

PASSLIB_HASHES = [
    ("correct horse", "$pbkdf2-sha256$29000$TWlNyRlDyPn/v3eu1VorpQ$1j9XXvY.hZNto4o8Izr5bwawcbj5qMspu8apAPC5ApY"),
    ("пароль-Ё1", "$pbkdf2-sha256$29000$aa313tt7L2XsvZeSck6pVQ$39DbR3xuOZ6z.5HZss5vogrFve.4kbRdWm3b9j7zY94"),
    ("x", "$pbkdf2-sha256$1000$17r3fq81JmTMmdOaM4awtg$zTyv2.5Vay8L1eP9PLKqHNMbI8dPvamMfg06Hn85TQs"),
    ("пароль", "$pbkdf2-sha256$1000$MDEyMzQ1Njc4OWFiY2RlZg$K2lNcecyyirtMcRrmsrKITw50FJj2YD0MqcgBz61dPc"),
]


@pytest.mark.parametrize("password, stored", PASSLIB_HASHES)
def test_every_hash_passlib_made_still_opens(password, stored):
    assert verify_password(password, stored) is True


@pytest.mark.parametrize("password, stored", PASSLIB_HASHES)
def test_a_wrong_password_does_not(password, stored):
    assert verify_password(password + "!", stored) is False


def test_a_new_hash_round_trips_and_is_salted():
    first, second = hash_password("секрет"), hash_password("секрет")
    assert first.startswith("$pbkdf2-sha256$29000$")
    assert first != second, "соль своя у каждого хеша"
    assert verify_password("секрет", first) and verify_password("секрет", second)
    assert not verify_password("Секрет", first)


def test_a_new_hash_is_one_passlib_would_accept():
    """Обратная совместимость: откат на код с passlib не должен запереть людей."""
    passlib = pytest.importorskip("passlib.hash")
    assert passlib.pbkdf2_sha256.verify("секрет", hash_password("секрет"))


@pytest.mark.parametrize("stored", [
    "", "plain-text", "$pbkdf2-sha256$abc$salt$hash", "$pbkdf2-sha1$29000$c2FsdA$aGFzaA",
    "$pbkdf2-sha256$29000$bad!salt$hash", "$pbkdf2-sha256$0$c2FsdA$aGFzaA", None,
])
def test_a_broken_or_foreign_hash_is_a_no_not_a_crash(stored):
    assert verify_password("whatever", stored) is False
