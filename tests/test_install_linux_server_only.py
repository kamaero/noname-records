"""Linux — только сервер: дикторы и режиссёры работают на Mac и Windows, «компьютер на Linux»
никому не нужен, а без домена сервер не откроешь дикторам. Скрипт зовём с --dry-run и
подменённым uname: проверка платформы стоит до него, Docker не трогается."""
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(tmp_path, system: str, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    uname = fake / "uname"
    uname.write_text(f"#!/bin/sh\necho {system}\n")
    uname.chmod(uname.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": f"{fake}:{os.environ['PATH']}", "HOME": str(tmp_path), **(env_extra or {})}
    return subprocess.run(["bash", str(ROOT / "install.sh"), *args], capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL, timeout=30)


def test_linux_without_a_domain_is_refused_with_a_pointer(tmp_path):
    result = _run(tmp_path, "Linux", "--dry-run")
    assert result.returncode == 2
    assert "только на сервер" in result.stderr and "Mac" in result.stderr and "Windows" in result.stderr


def test_linux_with_a_domain_goes_on(tmp_path):
    assert _run(tmp_path, "Linux", "--dry-run", "--domain", "studio.example.com").returncode == 0
    assert _run(tmp_path, "Linux", "--dry-run", env_extra={"DOMAIN": "studio.example.com"}).returncode == 0


def test_a_mac_needs_no_domain(tmp_path):
    assert _run(tmp_path, "Darwin", "--dry-run").returncode == 0
