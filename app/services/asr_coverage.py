"""Имя роли латиницей — чтобы имя файла, набранное латиницей, нашло своего персонажа.

Разбор реплик роли из текста v1 и сверка по нему жили здесь же и ушли вместе с v1:
сверка записи теперь идёт по разметке v2 (`app/services/asr_align.py`).
"""
from __future__ import annotations

from app.services.author_profile import normalize_name

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(value: str) -> str:
    """A Cyrillic name in Latin letters, so a Latin-typed filename can find it.

    One fixed direction on purpose: Cyrillic -> Latin is unambiguous, while reading
    Latin back is not (`sh` may be «ш» or «сх»). Callers compare the result to what
    the actor typed rather than trying to parse the actor's spelling.
    """
    return "".join(_TRANSLIT.get(ch, ch) for ch in normalize_name(value))


