"""Как называется аудиофайл в студии.

Одно имя в двух местах: то, что диктор набрал у себя на диске, и то, что лежит у нас
в базе. Раньше сервер переименовывал загруженное по-своему, кириллицей, и на один
файл приходилось два написания — как раз тот беспорядок, из-за которого никто не был
уверен, что и куда попало.

    утверждённая роль   KP_Ch01_Dgarnin_Zotov.wav
    проба на роль       KP_Ch01_Osniva_NataliGolubeva_proba.wav

Проба отличается от дубля одним хвостом. Глава у неё необязательна: читают пробу из
какого-то куска, но роль ещё не назначена, и требование главы — ровно то, что
заставляло диктора писать «пробы» внутрь имени файла.
"""
from __future__ import annotations

import re

from app.services.asr_coverage import transliterate

#: хвост, которым проба отличается от дубля
AUDITION_TAIL = "proba"


def latin_word(value: str) -> str:
    """«Слуга Билсима» → `SlugaBilsima`. Одно слово, латиницей, с заглавных.

    Латиница потому, что имя файла ездит через почту, DAW и чужие файловые системы, и
    кириллица там ломается тише, чем хотелось бы. Ударение снимается вместе с прочей
    диакритикой: «Дгарни́н» и «Дгарнин» — один и тот же персонаж.
    """
    folded = transliterate(str(value or ""))
    parts = [part for part in re.split(r"[^a-z0-9]+", folded.lower()) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts)


def book_token(book_code: str) -> str:
    """Код книги латиницей и заглавными: «КП» → `KP`. Он же и читается как код."""
    return latin_word(book_code).upper()


def chapter_token(chapter: str) -> str:
    """«Глава 32. Я ваш щит» → `Ch32`. Пусто — если номера в названии нет.

    Двузначный минимум, чтобы главы сортировались как числа, а не как строки: иначе
    десятая встаёт между первой и второй.
    """
    match = re.search(r"(\d+)", str(chapter or ""))
    if not match:
        return ""
    return f"Ch{int(match.group(1)):02d}"


def file_extension(original_filename: str, default: str = "wav") -> str:
    match = re.search(r"\.([a-zA-Z0-9]{1,8})$", str(original_filename or ""))
    return match.group(1).lower() if match else default


#: хвост канонического имени: `_fixN` и/или порядковый номер `_M` перед расширением
_NAME_SUFFIXES = re.compile(r"(?:_fix(\d+))?(?:_(\d+))?$", re.IGNORECASE)


def name_suffixes(canonical_filename: str) -> tuple[int | None, int]:
    """Номер фикса и порядковый номер из канонического имени: `(None, 1)` — обычный первый файл.

    Читается из уже выданного имени, а не пересчитывается по живым строкам: номер,
    однажды ставший частью ключа хранилища, должен пережить и удаление соседей, и
    переименование актёра — иначе два файла сходятся в одно имя.
    """
    stem = re.sub(r"\.[^.]+$", "", str(canonical_filename or ""))
    match = _NAME_SUFFIXES.search(stem)
    if match is None:
        return None, 1
    fix = int(match.group(1)) if match.group(1) else None
    ordinal = int(match.group(2)) if match.group(2) else 1
    return fix, max(1, ordinal)


def canonical_audio_name(
    *,
    book_code: str,
    chapter: str,
    role: str,
    actor_name: str,
    original_filename: str,
    kind: str = "take",
    ordinal: int = 1,
    fix: int | None = None,
) -> str:
    """Имя, под которым файл ляжет в студию, — и то, которым диктору его назвать."""
    parts = [book_token(book_code) or "BOOK"]
    chapter_part = chapter_token(chapter)
    if chapter_part:
        parts.append(chapter_part)
    parts.append(latin_word(role) or "Role")
    parts.append(latin_word(actor_name) or "Unknown")
    if kind == "audition":
        parts.append(AUDITION_TAIL)
    # Короткая пересъёмка реплики — не вторая полная запись роли, ей нужно
    # собственное, узнаваемое имя, а не порядковый номер вперемешку с дублями.
    if fix is not None and int(fix) >= 1:
        parts.append(f"fix{int(fix)}")
    # Вторая запись той же роли — не промах, ей нужен свой номер. И не только пробе:
    # без номера второй дубль получает имя первого, а значит и тот же ключ хранилища,
    # и затирает его на диске. Пока ключ начинался с uuid, номер был удобством; с
    # раскладкой по книге и главе он стал единственной защитой.
    if int(ordinal) > 1:
        parts.append(str(int(ordinal)))
    return "_".join(parts) + "." + file_extension(original_filename)
