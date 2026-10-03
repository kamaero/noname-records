from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.models import ScriptBook
from app.services.character_colors import normalize_character_key

SERVER_TIMEZONE = ZoneInfo("Asia/Yekaterinburg")


def get_book(db, book_id: str) -> ScriptBook | None:
    return db.query(ScriptBook).filter(ScriptBook.id == book_id).first()


def format_dt(value: datetime | None) -> str | None:
    if not value:
        return None
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return normalized.astimezone(SERVER_TIMEZONE).isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    value = value.strip().replace(" ", "_")
    return re.sub(r"[^a-zA-Zа-яА-Я0-9._-]", "", value)[:120] or "unknown"


def normalize_role_label(value: str) -> str:
    clean = re.sub(r"[\[\](){}\"'`]+", " ", value or "")
    clean = re.sub(r"\s+", " ", clean).strip(" -_.")
    return clean


def canonical_character_name(value: str) -> str:
    clean = normalize_character_key(normalize_role_label(value or ""))
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return ""
    return clean


NARRATOR_DISPLAY_NAME = "Рассказчик"
# Stable synthetic key so every narrator surface form collapses to ONE identity,
# overriding any per-row char_map_id. Cannot collide with a real character key.
NARRATOR_CANONICAL_KEY = "__narrator__"
# Normalized (accent-stripped, casefolded) narrator labels. NOTE: "автор"/"от автора"
# are treated as narrator; in the rare book where "Автор" is a real speaking character
# this misattributes — fix via the manual character reassign in the cast editor.
NARRATOR_ALIASES = frozenset({"рассказчик", "narrator", "автор", "от автора"})


def is_narrator_name(value: str) -> bool:
    key = canonical_character_name(value)
    if not key:
        return False
    # casefold here: canonical_character_name preserves case, NARRATOR_ALIASES is lower-case
    return key.casefold() in NARRATOR_ALIASES


def narrator_aware_aggregate_key(name: str, char_map_id: str | None, canonical_name: str) -> str:
    if is_narrator_name(name):
        return NARRATOR_CANONICAL_KEY
    return str(char_map_id or "").strip() or canonical_name


