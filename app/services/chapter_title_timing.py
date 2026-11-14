"""Оплаченное услышанное неизменно; сомнительное время не превращается в точную нарезку."""
from __future__ import annotations

from copy import deepcopy
import math
import re


_BRACKET_TAG = re.compile(r"^\s*\[[^\[\]]+\]\s*$")


def _bounds(item: dict, duration: float) -> tuple[float, float]:
    try:
        if isinstance(item["start"], bool) or isinstance(item["end"], bool):
            raise ValueError
        start, end = float(item["start"]), float(item["end"])
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError("invalid_word_timing") from None
    if not (math.isfinite(start) and math.isfinite(end) and 0 <= start <= end <= duration):
        raise ValueError("invalid_word_timing")
    return start, end


def _lexical(text: str) -> bool:
    return not _BRACKET_TAG.fullmatch(text) and any(character.isalnum() for character in text)


def prepare_title_segments(segments: list[dict], duration: float) -> tuple[list[dict], list[dict]]:
    """Оставить нулевое слово барьером, а не склеить соседей в ложный заголовок.

    Время не интерполируем и не исправляем. Проверка исходного порядка идёт до
    удаления пунктуации, чтобы очистка не спрятала повреждение оплаченного кэша.
    """
    try:
        if isinstance(duration, bool):
            raise ValueError
        duration = float(duration)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("invalid_word_timing") from None
    if not math.isfinite(duration) or duration < 0 or not isinstance(segments, list):
        raise ValueError("invalid_word_timing")
    derived, issues = [], []
    previous_start = 0.0
    positive_end = 0.0
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError("invalid_word_timing")
        if "start" in segment or "end" in segment:
            # ffprobe округляет длительность до миллисекунд, а ASR оставляет
            # дробную границу контейнера. Слова проверяем строго ниже; служебный
            # конец сегмента может отличаться только в пределах этого округления.
            _bounds(segment, duration + .001)
        words = segment.get("words", [])
        if not isinstance(words, list):
            raise ValueError("invalid_word_timing")
        if not words:
            if _lexical(str(segment.get("text") or "")):
                raise ValueError("missing_word_times")
            derived.append(deepcopy(segment))
            continue
        copied = deepcopy(segment)
        prepared = []
        for word in words:
            if not isinstance(word, dict) or not isinstance(word.get("word"), str):
                raise ValueError("invalid_word_timing")
            start, end = _bounds(word, duration)
            if start < previous_start or start < positive_end:
                raise ValueError("invalid_word_timing")
            previous_start = start
            if end > start:
                positive_end = end
            text = word["word"]
            item = deepcopy(word)
            if start == end:
                if _lexical(text):
                    item["_timing_unresolved"] = True
                    issues.append({"start": start, "end": end, "text": text,
                                   "reason": "invalid_word_timing", "candidates": []})
                elif not _BRACKET_TAG.fullmatch(text):
                    continue
            prepared.append(item)
        # Пустой words включил бы fallback по segment.text и вернул удалённое
        # через выдуманную интерполяцию; такой сегмент для матчера не нужен.
        if prepared:
            copied["words"] = prepared
            derived.append(copied)
    return derived, issues
