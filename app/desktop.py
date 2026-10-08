"""Точка входа настольной версии: оболочка (Tauri) запускает бэкенд этим модулем.

    python -m app.desktop --data-dir <папка> --port 0 --token <ключ запуска>

Порядок важен: окружение ставится ДО импорта приложения — настройки, ограничитель запросов
и пути читаются при импорте. Секреты сессии и шифрования ключей создаются один раз и живут в
папке данных: новые на каждом запуске сделали бы сохранённые ключи нейросетей нечитаемыми.
Оболочка ждёт строку NONAME_READY и, умирая, закрывает stdin — бэкенд не остаётся сиротой.
"""
import argparse
import json
import os
import secrets
import shutil
import socket
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXIT_ALREADY_RUNNING = 3


def load_or_create_secrets(data_dir: Path) -> dict:
    path = data_dir / "seat-secrets.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    from cryptography.fernet import Fernet

    values = {"secret_key": secrets.token_hex(48), "keys_encryption_key": Fernet.generate_key().decode()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(values), encoding="utf-8")
    os.chmod(tmp, 0o600)
    # через переименование: оборванная запись не оставит полфайла вместо секретов
    tmp.replace(path)
    return values


def acquire_lock(data_dir: Path):
    """Второй экземпляр на ту же папку делил бы базу и очередь — отказываем. Замок держит
    система, а не файл-флажок: упавшая программа не оставит «занято» навсегда."""
    handle = open(data_dir / "noname.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def backup_before_migration(db_file: Path, current: str | None, head: str) -> Path | None:
    """Копия базы рядом перед миграцией, которая её изменит: у человека на компьютере нет
    ночного бэкапа, а неудачная миграция без копии стоила бы ему всей разметки."""
    # Копия не нужна только новой (пустой) базе и уже свежей. «Нет ревизии» у непустой
    # базы — перенесённая старая: миграция изменит и её.
    if not db_file.exists() or db_file.stat().st_size == 0 or current == head:
        return None
    copy = db_file.with_name(f"{db_file.name}.before-{head}")
    shutil.copy2(db_file, copy)
    # журнал WAL, если остался от прошлого запуска, — часть базы
    for suffix in ("-wal", "-shm"):
        side = db_file.with_name(db_file.name + suffix)
        if side.exists():
            shutil.copy2(side, copy.with_name(copy.name + suffix))
    return copy


def _migrate(db_file: Path) -> None:
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    head = ScriptDirectory.from_config(config).get_current_head()
    current = None
    if db_file.exists():
        engine = create_engine(f"sqlite:///{db_file}")
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        engine.dispose()
    backup_before_migration(db_file, current, head)
    command.upgrade(config, "head")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.desktop")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(data_dir)
    if lock is None:
        print("Noname Records уже запущен с этой папкой данных.", file=sys.stderr)
        return EXIT_ALREADY_RUNNING
    found = load_or_create_secrets(data_dir)
    db_file = data_dir / "noname.db"
    os.environ.update({
        "SEAT_MODE": "one", "DATA_DIR": str(data_dir), "NONAME_SEAT_TOKEN": args.token,
        "DATABASE_URL": f"sqlite:///{db_file}", "AUDIO_STORAGE_PATH": str(data_dir / "recordings"),
        "SECRET_KEY": found["secret_key"], "KEYS_ENCRYPTION_KEY": found["keys_encryption_key"],
        # http на 127.0.0.1: Secure-cookie браузер бы не вернул
        "COOKIE_SECURE": "false", "TELEGRAM_NOTIFY_ENABLED": "false",
        # учётки администратора с паролем в настольной версии нет — вход по ключу запуска
        "ADMIN_PASSWORD_HASH": "", "AUDIO_NAS_PATH": "",
    })
    os.chdir(ROOT)  # app.main монтирует app/static относительно рабочего каталога
    _migrate(db_file)

    import uvicorn

    from app.db import SessionLocal
    from app.main import app
    from app.seat import mark_interrupted_runs

    with SessionLocal() as db:
        mark_interrupted_runs(db)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port))
    port = sock.getsockname()[1]
    from app.config import settings
    # порт известен только сейчас, а настройки уже прочитаны — ссылки в письмах и .sesx
    settings.app_base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    while not server.started and thread.is_alive():
        threading.Event().wait(0.05)
    if not server.started:
        print("Noname Records не смог запустить сервер.", file=sys.stderr)
        return 1
    print(f"NONAME_READY http://127.0.0.1:{port}", flush=True)

    sys.stdin.read()  # оболочка закрыла stdin (вышла или умерла) — пора выходить
    server.should_exit = True
    thread.join(timeout=15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
