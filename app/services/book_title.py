"""Типизированное имя книги: «Автор - "Название"».

Три поля держатся порознь не ради красоты:

* ``title`` — чистое название произведения. Из него выводится **код книги**
  (``app.services.audio_uploads.derive_book_code``), который актёры пишут в именах
  файлов, а сверка по нему ищет книгу. Автор, затёкший в ``title``, молча портит
  код: «Белозёровы - "Сказки волшебников. книга 2"» даёт ``RVK2`` вместо ``SVK2``.
* ``author_label`` — автор ровно так, как его показывают («Белозёровы»). Это витрина,
  а не канон: канонический ``Author`` («Александр Белозёров») живёт отдельно и
  подтягивает ростер и цвета реплик — см. ``app.services.author_detect``.
* ``display_title`` — собранная строка. Её читают все экраны, бот и заголовки
  проектов (``display_title or title``), поэтому собираем один раз при записи,
  а не в каждом месте показа.
"""
from __future__ import annotations

import re

#: кавычки, которые может принести человек или чужой файл
_OPEN = '"\u00ab\u201c\u201e'
_CLOSE = '"\u00bb\u201d\u201c'
_QUOTES = _OPEN + _CLOSE

#: «Автор - "Название"» — дефис с пробелами, название в кавычках любого вида.
_TYPED = re.compile(
    r"^\s*(?P<author>[^" + re.escape(_QUOTES) + r"]{1,120}?)\s+[-\u2013\u2014]\s+"
    r"[" + re.escape(_OPEN) + r"](?P<title>.+?)[" + re.escape(_CLOSE) + r"]\s*$"
)


def _clean(value: str) -> str:
    return re.sub(r"\s{2,}", " ", str(value or "").replace("\n", " ")).strip()


def split_display_title(text: str) -> tuple[str, str]:
    """Разобрать «Автор - "Название"» обратно на автора и название.

    Нужна для старых книг, у которых автор затёк в ``title`` ещё при загрузке.
    Строка не того вида — автор пустой, название возвращается как есть.
    """
    raw = _clean(text)
    match = _TYPED.match(raw)
    if not match:
        return "", raw
    return _clean(match.group("author")), _clean(match.group("title"))


def compose_display_title(author: str, title: str) -> str:
    """Собрать витрину. Без автора — просто название, без названия — просто автор."""
    clean_author, clean_title = _clean(author), _clean(title).strip(_QUOTES).strip()
    if clean_author and clean_title:
        return f'{clean_author} - "{clean_title}"'
    return clean_title or clean_author


def apply_book_title(book, *, author: str, title: str) -> str:
    """Записать в книгу все три поля разом и вернуть новую витрину.

    Пустое название отбрасывается: книга без ``title`` теряет код, по которому
    находят её записи, поэтому прежнее название остаётся.
    """
    clean_title = _clean(title).strip(_QUOTES).strip() or _clean(getattr(book, "title", ""))
    clean_author = _clean(author)
    book.title = clean_title
    book.author_label = clean_author[:120]
    book.display_title = compose_display_title(clean_author, clean_title)
    return book.display_title
