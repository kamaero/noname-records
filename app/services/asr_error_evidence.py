"""Личное письмо требует двух независимых расшифровок одного исходного окна.

Пропуск не сообщает, какое слово прозвучало вместо ожидаемого. Поэтому только
равные по длине блоки замен могут стать доказательством оговорки; неоднозначные
выравнивания остаются внутренней диагностикой покрытия.
"""
from __future__ import annotations

import math
from difflib import SequenceMatcher
from pathlib import PurePosixPath

from app.services.asr_align import normalize_words, word_stream


def primary_window(transcript: dict, start: float, end: float, *, language: str = "ru") -> dict:
    words = [(word, at, until) for word, at, until in word_stream(transcript.get("segments") or [], language=language)
             if at >= start and until <= end]
    return {"text": " ".join(word for word, _, _ in words),
            "words": [{"word": word, "start": at, "end": until} for word, at, until in words]}


def omission_only(expected: str, heard: str, *, language: str = "ru") -> bool:
    wanted, spoken = normalize_words(expected, language=language), normalize_words(heard, language=language)
    return all(kind in {"equal", "delete"} for kind, *_ in
               SequenceMatcher(None, wanted, spoken, autojunk=False).get_opcodes())


def _replacements(expected: list[str], heard: list[str]) -> dict[int, tuple[str, int]]:
    out = {}
    for kind, a, b, c, d in SequenceMatcher(None, expected, heard, autojunk=False).get_opcodes():
        if kind == "replace" and b - a == d - c:
            out.update({a + step: (heard[c + step], c + step) for step in range(b - a)})
    return out


def confirmed_error_items(flags: list[dict], *, source_names: dict | None = None,
                          allowed_source_ids: set | None = None, language: str = "ru") -> list[dict]:
    """Старый флаг без сохранённого второго слуха не превращается в обвинение."""
    output = []
    seen = set()
    for flag in flags if isinstance(flags, list) else []:
        if not isinstance(flag, dict):
            continue
        if flag.get("confirmed") is not True or flag.get("status") != "partial":
            continue
        if not isinstance(flag.get("text"), str):
            continue
        expected = normalize_words(flag["text"], language=language)
        missing_words = flag.get("missing_words")
        suspects = {word for text in missing_words if isinstance(text, str) for word in normalize_words(text, language=language)} if isinstance(missing_words, list) else set()
        proofs = flag.get("confirmation_evidence")
        for proof in proofs if isinstance(proofs, list) else []:
            if not isinstance(proof, dict):
                continue
            source_id = str(proof.get("source_audio_file_id") or "")
            if not source_id or proof.get("confirmed") is not True:
                continue
            if allowed_source_ids is not None and source_id not in allowed_source_ids:
                continue
            # Без привязки к исходному окну нельзя отличить услышанное в соседнем
            # дубле от ошибки в том фрагменте, который попал в главу.
            try:
                start, end = float(proof["start"]), float(proof["end"])
                if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end):
                    continue
            except (KeyError, TypeError, ValueError):
                continue
            windows = flag.get("source_windows")
            if not isinstance(windows, list) or not any(
                isinstance(window, dict) and str(window.get("source_audio_file_id") or "") == source_id
                and window.get("start") == start and window.get("end") == end for window in windows):
                continue
            if not isinstance(proof.get("primary_text"), str) or not isinstance(proof.get("secondary_text"), str):
                continue
            primary = normalize_words(proof["primary_text"], language=language)
            secondary = normalize_words(proof["secondary_text"], language=language)
            first, second = _replacements(expected, primary), _replacements(expected, secondary)
            for position, (heard, spoken_position) in first.items():
                if expected[position] not in suspects or second.get(position, (None,))[0] != heard:
                    continue
                at, until = start, end
                words = proof.get("primary_words")
                words = words if isinstance(words, list) else []
                # Время берём только у реально произнесённого слова, в шкале
                # исходника; при отсутствии точной связи показываем всё окно.
                timed = [(token, word.get("start"), word.get("end")) for word in words if isinstance(word, dict)
                         for token in normalize_words(str(word.get("word") or ""), language=language)]
                if spoken_position < len(timed) and timed[spoken_position][0] == heard:
                    try:
                        word_start, word_end = map(float, timed[spoken_position][1:])
                        if start <= word_start < word_end <= end:
                            at, until = word_start, word_end
                    except (TypeError, ValueError):
                        pass
                name = (source_names or {}).get(source_id) or ""
                filename = PurePosixPath(str(name).replace("\\", "/")).name or "Source file not specified"
                key = (source_id, at, until, expected[position], heard)
                if key in seen:
                    continue
                seen.add(key)
                output.append({"source_audio_file_id": source_id, "filename": filename,
                               "start": at, "end": until, "expected": expected[position],
                               "heard": heard, "context": str(flag.get("text") or "")})
    return output
