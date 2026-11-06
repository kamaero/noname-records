"""Запись главы не ложится в главу, где у её роли нет ни одной реплики (с 03.10).

Сверка «глава в имени файла ≠ глава в форме» (`chapter_mismatch`) ловит только
расхождение двух подсказок диктора. 03.10 диктор назвала файл `Ch01` и выбрала
в форме главу 1 — обе подсказки совпали, а «Мираэль» говорит только в главе 32.
Файл молча лёг в чужую главу: опись показала бы «ничью роль», распознавание оплачено
впустую. Решение владельца — жёсткий отказ, без «всё равно загрузить»: если разметка
ошиблась, сначала правят разметку в Читалке.

Правда о том, кто где говорит, — та же, что у описи главы (`chapter_role_counts`).
Отказ опирается только на знание: глава без разметки, неизвестная глава или книга —
не повод отказывать.
"""
from __future__ import annotations

from app.models import Character, ScriptBook, ScriptChapter
from app.services.audio_naming import book_token
from app.services.audio_uploads import derive_book_code
from app.services.author_profile import normalize_name
from app.services.chapter_delivery import _chapter_label, book_role_counts_by_chapter
from app.v2.cast_ops import chapter_role_counts, split_aliases


def _book_for_code(db, book_code: str):
    wanted = book_token(str(book_code or ""))
    if not wanted:
        return None
    for book in db.query(ScriptBook).all():
        if book_token(derive_book_code(str(book.title or ""))) == wanted:
            return book
    return None


def _role_keys(db, book_id: str, role: str) -> set[str]:
    """Все написания роли: как её назвали в форме, плюс имя и псевдонимы её персонажа —
    регистр и ударения не в счёт («мираэль» — та же Мираэль)."""
    key = normalize_name(str(role or ""))
    keys = {key} if key else set()
    for character in db.query(Character).filter(Character.book_id == book_id).all():
        names = {normalize_name(item) for item in [str(character.name or ""), *split_aliases(character.aliases)] if item}
        names.discard("")
        if key in names:
            keys |= names
    return keys


def role_not_in_chapter(db, *, book_code: str, chapter_label: str, role: str) -> list[int] | None:
    """`None` — запись ложится по делу или проверить не по чему. Список — роль в этой
    главе не говорит; в нём номера глав, где говорит (пустой — нигде по разметке)."""
    book = _book_for_code(db, book_code)
    label = str(chapter_label or "").strip()
    if book is None or not label or not str(role or "").strip():
        return None
    chapter = next(
        (row for row in db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id).all() if _chapter_label(row) == label),
        None,
    )
    if chapter is None:
        return None
    counts = chapter_role_counts(db, chapter.id)
    if not counts:
        return None
    keys = _role_keys(db, book.id, role)
    if any(normalize_name(name) in keys for name in counts):
        return None
    index_of = {
        str(row_id): int(index or 0)
        for row_id, index in db.query(ScriptChapter.id, ScriptChapter.chapter_index).filter(ScriptChapter.book_id == book.id).all()
    }
    return sorted(
        index_of[chapter_id]
        for chapter_id, by_role in book_role_counts_by_chapter(db, book.id).items()
        if chapter_id in index_of and any(normalize_name(name) in keys for name in by_role)
    )


def refusal_text(role: str, chosen_chapter: int, speaks_in: list[int]) -> str:
    """Отказ словами: диктор должен понять, куда грузить, а не только что нельзя."""
    head = f"у роли «{role}» нет реплик в главе {chosen_chapter}"
    if not speaks_in:
        return f"{head} — по разметке роль не говорит ни в одной главе"
    where = f"в главе {speaks_in[0]}" if len(speaks_in) == 1 else f"в главах {', '.join(str(n) for n in speaks_in)}"
    return f"{head} — роль говорит {where}"
