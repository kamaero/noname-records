"""Точка входа настольной версии: оболочка ждёт строку NONAME_READY и закрывает stdin при выходе.

Тесты запускают настоящий процесс на 127.0.0.1 — в сеть они не ходят.
Миграции выводят журнал в stderr: объединённый поток читает отдельный поток,
чтобы заполненный pipe не блокировал запуск. На NONAME_READY отведено 60 секунд;
при ошибке дочерний процесс завершается, последние строки попадают в диагностику."""
import json
import queue
import threading
from collections import deque
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DROP = ("DATABASE_URL", "AUDIO_STORAGE_PATH", "SEAT_MODE", "DATA_DIR", "NONAME_SEAT_TOKEN", "SECRET_KEY",
        "KEYS_ENCRYPTION_KEY")


def _env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in DROP}


def _start(data_dir: Path, token: str = "z" * 48) -> tuple[subprocess.Popen, str]:
    # Alembic пишет в stderr больше размера pipe: ожидание одного stdout
    # блокировало реальный запуск до NONAME_READY. Читаем оба потока вместе.
    proc = subprocess.Popen([sys.executable, "-m", "app.desktop", "--data-dir", str(data_dir), "--port", "0",
                             "--token", token], cwd=ROOT, env=_env(), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    diagnostics = deque(maxlen=200)
    ready = queue.Queue(maxsize=1)

    def read_output():
        for line in proc.stdout:
            diagnostics.append(line)
            if line.startswith("NONAME_READY "):
                ready.put_nowait(line.split()[1])
        try:
            ready.put_nowait(None)
        except queue.Full:
            pass

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        url = ready.get(timeout=60)
    except queue.Empty:
        url = None
    if url:
        return proc, url
    # При ошибке старт не должен оставлять сервер или блокировать весь сьют.
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    reader.join(timeout=1)
    raise AssertionError("Desktop did not report NONAME_READY within 60 seconds:\n" + "".join(diagnostics))


def _stop(proc: subprocess.Popen) -> int:
    proc.stdin.close()
    return proc.wait(timeout=20)


def test_starts_answers_and_dies_with_the_shell(tmp_path):
    data = tmp_path / "Тестовый Пользователь" / "Noname Records"
    proc, url = _start(data)
    try:
        assert url.startswith("http://127.0.0.1:")
        with urllib.request.urlopen(f"{url}/health", timeout=5) as response:
            assert response.status == 200
        assert (data / "noname.db").exists()
    finally:
        assert _stop(proc) == 0


def test_the_launch_token_opens_the_app(tmp_path):
    proc, url = _start(tmp_path, token="q" * 48)
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        with opener.open(f"{url}/seat?token={'q' * 48}", timeout=10) as response:
            assert response.status == 200  # перенаправление на /app/ прошло
        with opener.open(f"{url}/api/me", timeout=10) as response:
            me = json.loads(response.read())
        assert me["authenticated"] and me["seat_mode"] == "one"
    finally:
        _stop(proc)


def test_secrets_survive_a_restart(tmp_path):
    proc, _ = _start(tmp_path)
    _stop(proc)
    first = json.loads((tmp_path / "seat-secrets.json").read_text())
    proc, _ = _start(tmp_path)
    _stop(proc)
    assert json.loads((tmp_path / "seat-secrets.json").read_text()) == first
    if os.name != "nt":
        assert oct((tmp_path / "seat-secrets.json").stat().st_mode & 0o777) == "0o600"


def test_a_second_instance_on_the_same_folder_is_refused(tmp_path):
    proc, _ = _start(tmp_path)
    try:
        second = subprocess.run([sys.executable, "-m", "app.desktop", "--data-dir", str(tmp_path), "--port", "0",
                                 "--token", "y" * 48], cwd=ROOT, env=_env(), capture_output=True, text=True,
                                timeout=60)
        assert second.returncode == 3 and "already running" in second.stderr
    finally:
        _stop(proc)


def test_backup_only_when_migration_will_change_the_db(tmp_path):
    from app.desktop import backup_before_migration
    db = tmp_path / "noname.db"
    db.write_bytes(b"sqlite")
    assert backup_before_migration(db, current="0045_spend", head="0045_spend") is None
    copy = backup_before_migration(db, current="0045_spend", head="0046_onboarding")
    assert copy.name == "noname.db.before-0046_onboarding" and copy.read_bytes() == b"sqlite"
    assert backup_before_migration(tmp_path / "absent.db", current=None, head="0046_onboarding") is None


def test_an_existing_db_without_a_revision_is_copied_too(tmp_path):
    # «нет ревизии» — не только новая база: перенесённая старая без alembic_version тоже
    # изменится миграцией, и копия ей нужна не меньше
    import sqlite3

    from app.desktop import backup_before_migration
    db = tmp_path / "noname.db"
    with sqlite3.connect(db) as conn:
        conn.execute("create table notes (t text)")
        conn.execute("insert into notes values ('дорогая строка')")
    copy = backup_before_migration(db, current=None, head="0046_onboarding")
    assert copy is not None
    with sqlite3.connect(copy) as conn:
        assert conn.execute("select t from notes").fetchone() == ("дорогая строка",)
    empty = tmp_path / "fresh.db"
    empty.write_bytes(b"")  # файл, который создал сам SQLite при первом подключении
    assert backup_before_migration(empty, current=None, head="0046_onboarding") is None
