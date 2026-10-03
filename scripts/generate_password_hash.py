#!/usr/bin/env python3
"""Хеш пароля для ADMIN_PASSWORD_HASH (формат `$pbkdf2-sha256$...`, см. app/services/passwords.py)."""
import os
import sys
from getpass import getpass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.passwords import hash_password  # noqa: E402

password = getpass("Введите пароль для ADMIN: ")
print(hash_password(password))
