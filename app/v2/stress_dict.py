"""Base stress dictionary (STRESS_BASE_DICT_PATH) used by the v2 stress step.

Moved from the v1 stress package `app.services.stress.base_dict` when that package went.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.config import settings
from app.pronunciation import _strip_stress

_CACHE: dict[str, list[str]] | None = None
_LOCK = threading.Lock()


def _normalize(word: str) -> str:
    return _strip_stress((word or "").strip()).casefold()


def _coerce_forms(value) -> list[str]:
    forms = value if isinstance(value, list) else [value]
    return [str(f).strip() for f in forms if str(f).strip()]


def _parse_loaded_json(raw) -> dict[str, list[str]]:
    """Adapter from the file's JSON into {normalized_word: [stressed_forms]}.

    Supports the noname mega-orthoepic schema
    (`{"forms": {word: "сло́во"}, "ambiguous": {word: ["за́мок", "замо́к"]}, "counts": ..., ...}`)
    and a flat fallback (`{word: "сло́во" | ["a", "b"]}`). A single stressed form → lookup
    returns the string; multiple (homograph) → returns the list.
    """
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    forms = raw.get("forms")
    ambiguous = raw.get("ambiguous")
    if isinstance(forms, dict) or isinstance(ambiguous, dict):
        if isinstance(forms, dict):
            for key, value in forms.items():
                coerced = _coerce_forms(value)
                if coerced:
                    out[_normalize(key)] = coerced
        if isinstance(ambiguous, dict):
            for key, value in ambiguous.items():  # homographs override single forms
                coerced = _coerce_forms(value)
                if coerced:
                    out[_normalize(key)] = coerced
        return out
    # Flat fallback shape.
    for key, value in raw.items():
        coerced = _coerce_forms(value)
        if coerced:
            out[_normalize(key)] = coerced
    return out


def _load() -> dict[str, list[str]]:
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    with _LOCK:
        if _CACHE is not None:
            return _CACHE
        path = (settings.stress_base_dict_path or "").strip()
        data: dict[str, list[str]] = {}
        if path:
            # Path.exists() used to sit outside this try. In Python 3.12 it swallows
            # "not found" but re-raises PermissionError, so an unreadable dictionary
            # escaped as an exception from the first looked-up word — which is the end
            # of the FINAL stage. Chapter 15 of «Крылья полумрака» lost 102 minutes of
            # model time that way. The loud check is ensure_available(), called before
            # the model runs; here we stay quiet and empty.
            try:
                data = _parse_loaded_json(json.loads(Path(path).read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, AttributeError):
                data = {}
        _CACHE = data
        return _CACHE


def ensure_available() -> None:
    """Fail loudly, and EARLY, when a configured dictionary cannot be read.

    Call this before spending the model budget. An empty setting means stress
    marking is simply not configured, which is a choice and not a fault.
    """
    path = (settings.stress_base_dict_path or "").strip()
    if not path:
        return
    try:
        with open(path, "rb") as handle:
            handle.read(1)
    except OSError as exc:
        raise RuntimeError(
            f"stress dictionary is configured but unreadable: {path} ({exc.strerror}). "
            f"Check STRESS_BASE_DICT_PATH — after the tier migration it pointed at the "
            f"dev checkout, which the service user cannot read."
        ) from exc


def reset_cache() -> None:
    global _CACHE
    _CACHE = None


def lookup(word: str):
    forms = _load().get(_normalize(word))
    if not forms:
        return None
    return forms[0] if len(forms) == 1 else list(forms)
