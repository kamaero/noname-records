"""Is a word rare enough that a reader in full flow may stress it wrong?

The reader's «только редкие» mode shows a stress mark only on such words (and on every
mark a person set). Rare means: not in `common_words_ru.txt`, the forms met at least
once per million words in living Russian (see `scripts/v2/build_common_words.py`).

Frequency, not a guess about which stresses are «tricky»: a model of analogies by word
endings was tried on the narrator's own mistakes (карли́ца, факело́в, сомнамбу́ла) and
called «ка́рлица» safe because «у́лица» outweighs «столи́ца». A rule an actor can
predict beats a clever one he cannot.
"""
from __future__ import annotations

import threading
from pathlib import Path

_PATH = Path(__file__).with_name("common_words_ru.txt")
_COMMON: frozenset[str] | None = None
_LOCK = threading.Lock()


def _common() -> frozenset[str]:
    global _COMMON
    if _COMMON is None:
        with _LOCK:
            if _COMMON is None:
                try:
                    lines = _PATH.read_text(encoding="utf-8").splitlines()
                except OSError:
                    lines = []
                words = {line.strip() for line in lines if line.strip() and not line.startswith("#")}
                # a book that drops «ё» prints «еще»; the list may know only «ещё»
                _COMMON = frozenset(words | {word.replace("ё", "е") for word in words})
    return _COMMON


def is_rare(word: str) -> bool:
    """True unless the word, or its «е» spelling of a «ё», is a common form."""
    low = (word or "").replace("́", "").strip().lower()
    if not low:
        return False
    common = _common()
    return low.replace("ё", "е") not in common and low not in common
