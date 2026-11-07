"""Выбор установщика проверяется до записи .env, без Docker и сетевых запросов."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("selected", [None, "America/New_York", "Unknown/Nowhere"])
def test_unattended_install_keeps_utc_default_and_accepts_iana_choice(tmp_path, selected):
    root = Path(__file__).resolve().parents[1]
    shutil.copy(root / "install.sh", tmp_path / "install.sh")
    (tmp_path / "scripts").mkdir()
    shutil.copy(root / "scripts/validate_studio_timezone.py", tmp_path / "scripts/validate_studio_timezone.py")
    (tmp_path / "app/services").mkdir(parents=True)
    shutil.copy(root / "app/services/studio_calendar.py", tmp_path / "app/services/studio_calendar.py")
    tools = tmp_path / "bin"
    tools.mkdir()
    docker = tools / "docker"
    docker.write_text(f'''#!{sys.executable}
import subprocess, sys
args=sys.argv[1:]
if "scripts/validate_studio_timezone.py" in args:
    raise SystemExit(subprocess.call([sys.executable, "scripts/validate_studio_timezone.py", args[-1]]))
if "scripts/generate_password_hash.py" in args:
    print("$pbkdf2-sha256$test-hash")
if "scripts/generate_encryption_key.py" in args:
    print("a"*43+"=")
''')
    docker.chmod(0o755)
    curl = tools / "curl"
    curl.write_text("#!/bin/sh\nexit 0\n")
    curl.chmod(0o755)
    command = ["bash", "install.sh", "--yes", "--domain", "studio.example.com"]
    if selected:
        command += ["--timezone", selected]
    result = subprocess.run(command, cwd=tmp_path, env={"PATH": f"{tools}:/usr/bin:/bin", "HOME": str(tmp_path),
                                                       "ADMIN_PASSWORD": "test-password"},
                            capture_output=True, text=True, timeout=20)
    if selected == "Unknown/Nowhere":
        assert result.returncode != 0
        assert "Invalid studio IANA timezone" in result.stderr
        assert not (tmp_path / ".env").exists()
        return
    assert result.returncode == 0, result.stderr
    assert f"STUDIO_TIMEZONE={selected or 'UTC'}\n" in (tmp_path / ".env").read_text()
    assert f"Requested studio timezone: {selected or 'UTC'}" in result.stdout
    assert "existing data keeps its legacy calendar" in result.stdout


def test_timezone_validator_rejects_bad_zone_without_config_or_database():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "scripts/validate_studio_timezone.py", "Unknown/Nowhere"],
                            cwd=root, capture_output=True, text=True)
    assert result.returncode != 0
    assert "Invalid studio IANA timezone" in result.stderr


def test_windows_installer_validates_timezone_before_writing_environment():
    source = (Path(__file__).resolve().parents[1] / "install.ps1").read_text(encoding="utf-8-sig")
    assert "'UTC'" in source and "--timezone" in source
    assert source.index("scripts/validate_studio_timezone.py") < source.index("$envContent =")
    assert "STUDIO_TIMEZONE=$StudioTimezone" in source
