"""Миграции при старте не глушат журнал приложения (02.10).

`alembic/env.py` настраивал логи через `fileConfig(...)` с умолчанием
`disable_existing_loggers=True`: после старта службы все уже созданные логгеры приложения
молчали — «Telegram login rejected» и прочие предупреждения не доходили до журнала, и
причину отказа во входе пришлось искать без них.
"""
import logging

from app import auth_routes  # noqa: F401 — логгер модуля создаётся при импорте
from app.db import run_migrations


def test_migrations_leave_the_app_loggers_alive(tmp_path, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'm.db'}")
    logger = logging.getLogger("app.auth_routes")
    logger.disabled = False
    run_migrations()
    assert logger.disabled is False
