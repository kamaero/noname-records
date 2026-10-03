#!/usr/bin/env python
"""Свести имена актёров к одному виду: «Фамилия Имя», как в телефонной книге.

Один человек разошёлся по базе двумя написаниями — «Уткина Наташа» в книге и
«Наташа Уткина» в учётке. Сравнение имён (`names_match`) считает их одним и тем же,
поэтому уведомления не путались, но таблица каста, пересечения ролей и смета
группируют по строке — и показывали двух актёров вместо одного.

Порядок слов определяется по двум признакам, а не по позиции:
* фамилия — по окончанию (-ов/-ев/-ин/-ский/-ко/-ук/-ян/-ова/-ина/-ская…);
* имя — по словарю, который собирается из САМОЙ базы: вторые слова тех записей,
  где первое слово уже опознано фамилией. Поэтому «Многорукий резидент», «Эн Джи»
  и «Max Ray» остаются как есть — в них не опознаётся ни имя, ни фамилия.

Нетронутым остаётся и то, что несёт смысл помимо имени: хвостовой «?» (актёр лишь
предложен) отрезается перед разбором и возвращается на место, скобочное пояснение
(«Аверина Виктория (Тори-А)») разбору не подлежит и едет следом.

    python scripts/normalize_actor_names.py                    # отчёт
    python scripts/normalize_actor_names.py --apply
    python scripts/normalize_actor_names.py --rename "Галинов Михаил=Галанов Михаил" --apply
"""
from __future__ import annotations

import argparse
import re
import sys

sys.path.insert(0, ".")

from sqlalchemy import update  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    AuthorCharacter,
    BookBudget,
    Character,
    RoleVote,
    TelegramAuthAccount,
    User,
)
from app.services.audio_rename import rename_actor_in_audio  # noqa: E402

#: (модель, поле) — все места, где лежит имя человека, кроме записей
FIELDS = [
    (User, "display_name"),
    (TelegramAuthAccount, "display_name"),
    (Character, "actor_name"),
    (AuthorCharacter, "actor_name"),
    (BookBudget, "narrator_actor_name"),
    (RoleVote, "actor_name"),
]
#: Записи переименовываются НЕ обновлением графы: каноническое имя файла (под ним файл
#: ложится в архив главы) собирается из имени актёра, а вид записи — дубль или проба —
#: решается тем, своя ли это роль. И то и другое пересчитывает `rename_actor_in_audio`.

#: окончания фамилий; женские формы идут первыми, иначе «-ова» съест «-ов»
SURNAME_SUFFIXES = (
    "ова", "ева", "ёва", "ина", "ына", "ская", "цкая", "ская", "ыха",
    "ов", "ев", "ёв", "ин", "ын", "ский", "цкий", "ской", "ко", "ук", "юк",
    "ян", "дзе", "швили", "енко", "чук", "ых", "их",
)

#: личные имена, которые сами кончаются как фамилия: «Илона», «Полина», «Марина».
#: Без этого списка «Мейз Илона» переворачивается в «Илона Мейз» — фамилия «Мейз»
#: ни на что не похожа, а «Илона» похожа на «-ина». Словарь из базы их не ловит:
#: он собирает вторые слова там, где первое уже опознано фамилией.
GIVEN_LIKE_SURNAME = {
    "регина", "полина", "марина", "ирина", "алина", "кристина", "карина", "ангелина",
    "валентина", "галина", "екатерина", "дина", "нина", "зина", "мальвина", "эвелина",
    "альбина", "аделина", "каролина", "сабина", "агнина", "янина", "христина",
}

_CYRILLIC = re.compile(r"^[А-Яа-яЁё][А-Яа-яЁё\-]*$")


def looks_like_surname(token: str) -> bool:
    low = token.casefold()
    return len(low) >= 4 and low.endswith(SURNAME_SUFFIXES)


def split_name(raw: str) -> tuple[str, str, str]:
    """(разбираемая часть, хвост, знак вопроса) — хвост это скобки и всё после них."""
    text = " ".join(str(raw or "").split())
    mark = "?" if text.endswith("?") else ""
    if mark:
        text = text[:-1].strip()
    tail = ""
    bracket = text.find("(")
    if bracket >= 0:
        tail = text[bracket:].strip()
        text = text[:bracket].strip()
    return text, tail, mark


def build_given_names(names: list[str]) -> set[str]:
    """Имена — вторые слова записей, где первое уже опознано фамилией."""
    given: set[str] = set()
    for raw in names:
        head, _tail, _mark = split_name(raw)
        parts = head.split()
        if len(parts) != 2:
            continue
        first, second = parts
        if not (_CYRILLIC.match(first) and _CYRILLIC.match(second)):
            continue
        if looks_like_surname(first) and not looks_like_surname(second):
            given.add(second.casefold())
    return given


def normalized(raw: str, given: set[str]) -> str:
    head, tail, mark = split_name(raw)
    parts = head.split()
    if len(parts) != 2:
        return raw
    first, second = parts
    if not (_CYRILLIC.match(first) and _CYRILLIC.match(second)):
        return raw
    first_given, second_given = first.casefold() in given, second.casefold() in given
    first_sur, second_sur = looks_like_surname(first), looks_like_surname(second)

    # Словарь главнее окончания: «Илона» кончается как фамилия, но это имя.
    if second_given and not first_given:
        return raw
    if first_given and not second_given:
        rebuilt = f"{second} {first}"
        return " ".join(filter(None, [rebuilt, tail])) + mark
    # дальше — только по окончанию фамилии
    if first_sur and not second_sur:
        return raw
    if second_sur and not first_sur:
        rebuilt = f"{second} {first}"
        return " ".join(filter(None, [rebuilt, tail])) + mark
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rename", action="append", default=[], metavar="СТАРОЕ=НОВОЕ",
                        help="точная замена до нормализации (например, опечатка в фамилии)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    manual = {}
    for item in args.rename:
        old, _, new = item.partition("=")
        if old.strip() and new.strip():
            manual[old.strip()] = new.strip()

    with SessionLocal() as db:
        names: set[str] = set()
        for model, field in FIELDS:
            column = getattr(model, field)
            names |= {str(value or "").strip() for (value,) in db.query(column).distinct() if str(value or "").strip()}
        given = build_given_names(sorted(names)) | GIVEN_LIKE_SURNAME
        print(f"имён в базе: {len(names)}; опознано личных имён: {len(given)}\n")

        mapping: dict[str, str] = {}
        for raw in sorted(names):
            target = manual.get(raw, raw)
            target = normalized(target, given)
            if target != raw:
                mapping[raw] = target

        print(f"=== переименования: {len(mapping)} ===")
        for old, new in sorted(mapping.items()):
            note = "  (ручная правка)" if old in manual else ""
            print(f"  {old}  →  {new}{note}")

        untouched = [n for n in sorted(names) if n not in mapping and len(split_name(n)[0].split()) == 2]
        odd = [n for n in untouched if not looks_like_surname(split_name(n)[0].split()[0])]
        if odd:
            print(f"\n=== оставлено как есть (порядок не опознан): {len(odd)} ===")
            for name in odd:
                print(f"  = {name}")

        if not args.apply:
            from app.models import AudioFile

            takes = sum(
                db.query(AudioFile).filter(AudioFile.actor_name == old).count()
                for old in mapping
            )
            print(f"\nзаписей с прежним именем актёра: {takes} — им пересчитается "
                  "каноническое имя файла (имя на диске не меняется)")
            print("\nОтчёт без записи (--apply, чтобы записать).")
            return 0

        total = 0
        for model, field in FIELDS:
            column = getattr(model, field)
            for old, new in mapping.items():
                result = db.execute(update(model).where(column == old).values(**{field: new}))
                total += int(result.rowcount or 0)
        # Записи — после каста: вид записи решается по тому, утверждён ли актёр на роль,
        # а значит смотреть надо на уже переименованный каст.
        db.flush()
        audio = sum(rename_actor_in_audio(db, was=old, now=new) for old, new in mapping.items())
        db.commit()
        print(f"\nЗаписано: строк обновлено {total}, записей переименовано {audio}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
