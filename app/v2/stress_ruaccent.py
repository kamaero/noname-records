"""RUAccent as the context layer: stress for homographs and out-of-dictionary words.

RUAccent (Den4ikAI) reads a sentence and returns it with `+` before each stressed
vowel (`зам+ок`). It is optional — an import that fails means the layer is skipped,
not that stress marking stops — and it is never trusted with the text: it drops
punctuation («–»), glues «Уль’Тахан» into «УльТахан», and may restore «ё». Only the
stress positions are taken, by aligning its words back onto the words of the
original text. Everything that touches the library is in this module so the resolver
in `app/v2/stress.py` can be tested without it.

The library breaks with transformers ≥ 5: the tokenizer stopped returning
`token_type_ids`, which the ONNX accent model still requires. Rather than editing the
installed package, `get_ruaccent()` wraps the model's tokenizer so the missing input
is added at call time.
"""
from __future__ import annotations

import difflib
import re
import threading
from collections.abc import Mapping

from app.v2.stress import ACUTE, cyrillic_runs, find_words, is_vowel

OMOGRAPH_MODEL_SIZE = "turbo3.1"

_LOCK = threading.Lock()
_INSTANCE = None
_UNAVAILABLE = False

_OUT_TOKEN_RE = re.compile(r"[А-Яа-яЁё+]+")


class _TokenizerWithTypeIds:
    """The accent model's tokenizer, returning the `token_type_ids` transformers 5 dropped."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def __call__(self, *args, **kwargs):
        import numpy as np

        encoded = self._inner(*args, **kwargs)
        if "token_type_ids" not in encoded:
            encoded["token_type_ids"] = np.zeros_like(encoded["input_ids"])
        return encoded

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _patch_token_type_ids(accent) -> None:
    model = getattr(accent, "accent_model", None)
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is not None and not isinstance(tokenizer, _TokenizerWithTypeIds):
        model.tokenizer = _TokenizerWithTypeIds(tokenizer)


def get_ruaccent():
    """The loaded RUAccent, or None when the library is not installed. Loads once (~20 s)."""
    global _INSTANCE, _UNAVAILABLE
    if _INSTANCE is not None:
        return _INSTANCE
    if _UNAVAILABLE:
        return None
    with _LOCK:
        if _INSTANCE is not None:
            return _INSTANCE
        load_kwargs = {}
        from app.seat import one_seat
        if one_seat():
            from app.services import stress_model
            # Настольная версия: модель только скачанная по кнопке, из папки данных. Без неё
            # слой пропускается, но не навсегда — скачают, и заработает без перезапуска.
            if not stress_model.is_ready():
                return None
            load_kwargs = {"workdir": str(stress_model.model_dir())}
        try:
            from ruaccent import RUAccent
        except ImportError:
            _UNAVAILABLE = True
            return None
        accent = RUAccent()
        accent.load(omograph_model_size=OMOGRAPH_MODEL_SIZE, use_dictionary=True, tiny_mode=False, **load_kwargs)
        _patch_token_type_ids(accent)
        _INSTANCE = accent
        return _INSTANCE


def _key(word: str) -> str:
    return word.replace("+", "").replace(ACUTE, "").lower().replace("ё", "е")


def _plus_offset(token: str) -> int | None:
    """Offset of the stressed vowel inside the token with its `+` removed."""
    pos = token.find("+")
    if pos < 0:
        return None
    return pos


def align_plus_marks(text: str, marked: str) -> dict[int, int]:
    """{index into find_words(text): vowel offset} read off RUAccent's `+`-marked output.

    Both sides are tokenised on Cyrillic runs and matched as sequences; where RUAccent
    glued several source words into one (apostrophes, non-breaking hyphens), the glued
    token is split back by length so the `+` lands in the right part.
    """
    runs = cyrillic_runs(text)
    src_keys = [_key(text[s:e]) for s, e in runs]
    tokens = _OUT_TOKEN_RE.findall(marked or "")
    out_keys = [_key(t) for t in tokens]

    pairs: dict[int, int] = {}  # run index -> offset

    def take(run_index: int, offset: int | None) -> None:
        if offset is None:
            return
        s, e = runs[run_index]
        word = text[s:e]
        if ACUTE in word:
            return
        if 0 <= offset < len(word) and is_vowel(word[offset]):
            pairs[run_index] = offset

    matcher = difflib.SequenceMatcher(None, src_keys, out_keys, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                take(i1 + k, _plus_offset(tokens[j1 + k]))
            continue
        if tag != "replace":
            continue
        i = i1
        for j in range(j1, j2):
            if i >= i2:
                break
            token, key = tokens[j], out_keys[j]
            if src_keys[i] == key:
                take(i, _plus_offset(token))
                i += 1
                continue
            glued = 0
            parts: list[int] = []
            k = i
            while k < i2 and glued < len(key) and key.startswith(src_keys[k], glued):
                glued += len(src_keys[k])
                parts.append(k)
                k += 1
            if glued != len(key) or len(parts) < 2:
                break
            offset = _plus_offset(token)
            if offset is not None:
                consumed = 0
                for part in parts:
                    length = len(src_keys[part])
                    if offset < consumed + length:
                        take(part, offset - consumed)
                        break
                    consumed += length
            i = k

    needed = set(find_words(text))
    word_index = {i: n for n, i in enumerate(i for i, run in enumerate(runs) if run in needed)}
    return {word_index[i]: offset for i, offset in pairs.items() if i in word_index}


def context_stress(text: str, *, accent=None) -> Mapping[int, int] | None:
    """The context layer for `Resolver`: None when RUAccent is unavailable."""
    accent = accent or get_ruaccent()
    if accent is None:
        return None
    return align_plus_marks(text, accent.process_all(text))


def context_layer():
    """`context_stress` bound to the loaded library, or None to skip the layer."""
    return context_stress if get_ruaccent() is not None else None
