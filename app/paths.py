"""Где лежат данные пользователя. Одно место, потому что в настольной версии рабочий каталог —
внутри пакета программы (туда писать нельзя), а данные — в папке программ системы."""
from pathlib import Path

from app.config import settings


def data_dir() -> Path:
    # читается при каждом вызове: тесты и точка входа меняют настройку после импорта
    return Path(settings.data_dir or "data").expanduser().resolve()


def data_path(*parts: str) -> Path:
    return data_dir().joinpath(*parts)
