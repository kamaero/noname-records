"""Каст цикла из таблицы студии: одна строка на актёра, роли через запятую.

Деление по запятой безопасно, только если ни одно имя роли не содержит запятой, а
скобки парные, — проверяйте свою таблицу, прежде чем на неё опираться. Строки с
маленькой буквы («щитник», «наложница») — эпизодические роли, описанные, а не
названные, а не обломки неудачного деления.

Таблица — опора, которой нет у легенд глав: легенда покрывает одну главу и может
не назвать актёра, а таблица держит весь цикл. Из неё стоит заполнять профиль автора.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.author_profile import normalize_name


@dataclass
class CastRole:
    role: str
    actors: set[str] = field(default_factory=set)


def split_roles(cell: str | None) -> list[str]:
    """One workbook cell into role names, dropping the empty tail of a trailing comma."""
    return [part.strip() for part in str(cell or "").split(",") if part.strip()]


def load_cast(*, path=None, rows=None) -> dict[str, CastRole]:
    """`{folded role name: CastRole}` from the workbook, or from rows in tests.

    Folding is the profile's own, so a stress mark written in one place and not the
    other does not split a character in two.
    """
    if rows is None:
        import openpyxl

        sheet = openpyxl.load_workbook(str(path), data_only=True)["Каст"]
        rows = list(sheet.iter_rows(min_row=2, values_only=True))

    cast: dict[str, CastRole] = {}
    for row in rows:
        if not row:
            continue
        actor = str(row[0] or "").strip()
        for role in split_roles(row[1] if len(row) > 1 else ""):
            key = normalize_name(role)
            if not key:
                continue
            entry = cast.setdefault(key, CastRole(role=role))
            if actor:
                entry.actors.add(actor)
    return cast
