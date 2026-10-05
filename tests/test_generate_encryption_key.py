"""Ключ шифрования, который пишет установщик, должен открываться тем же Fernet, что в приложении."""
import subprocess
import sys

from cryptography.fernet import Fernet


def test_the_script_prints_a_working_fernet_key():
    out = subprocess.run([sys.executable, "scripts/generate_encryption_key.py"], capture_output=True, text=True, check=True)
    key = out.stdout.strip()
    assert len(key) == 44
    assert Fernet(key.encode()).decrypt(Fernet(key.encode()).encrypt(b"x")) == b"x"
