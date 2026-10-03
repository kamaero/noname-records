"""K4: overlay the author cast (voice actor + reply colour) onto canon characters.

The author cast (`author_characters` in noname.db) is production ground truth —
who voices whom. This matches each canon character to a cast entry alias-aware
(same identity-key normalization the rest of the system uses) and returns the
actor + colour to attach. Pure — the DB wiring lives in the overlay script.
"""
from __future__ import annotations

from app.services.character_names import _normalize_character_identity_key as _key


def _keys(name: str, aliases: list[str]) -> set[str]:
    return {k for k in ({_key(name)} | {_key(a) for a in aliases}) if k}


def build_cast_index(cast: list[tuple]) -> list[tuple]:
    """cast: list of (name, aliases:list[str], actor:str, color:str)."""
    return [(_keys(n, al), actor or "", color or "") for n, al, actor, color in cast]


def match_cast(name: str, aliases: list[str], cast_index: list[tuple]) -> tuple[str, str]:
    """Return (actor, color) of the first cast entry sharing an identity key, else ("", "")."""
    ek = _keys(name, aliases)
    if not ek:
        return "", ""
    for ckeys, actor, color in cast_index:
        if ek & ckeys:
            return actor, color
    return "", ""
