"""Alembic migration environment for Noname Records.

The DB URL and engine come from app.config/app.db so migrations always target the
same database as the app. SQLite needs render_as_batch=True for ALTER operations.
"""
from logging.config import fileConfig

from alembic import context

from app.config import settings
from app.db import Base, engine

# Importing the models package populates Base.metadata for autogenerate.
import app.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    # Не выключать уже созданные логгеры: миграции идут на старте службы, и по умолчанию
    # fileConfig глушил весь журнал приложения (02.10: отказы входа через Telegram не писались).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata

_is_sqlite = settings.database_url.startswith("sqlite")


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=_is_sqlite,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
