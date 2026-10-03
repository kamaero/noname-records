"""Collapse per-character DB rows into the identities the budget view prices.

Split out of the v1 dashboard code: the budget screen is the only consumer left.
Rows sharing a `char_map_id` (or, without one, a normalised name) become one row;
every narrator spelling collapses into the single NARRATOR identity, mirroring
`shared_runtime.narrator_aware_aggregate_key` used by the DB rebuild path.
"""
from __future__ import annotations

import re

from app.models import Character, CharacterBudgetSnapshot
from app.services.character_colors import normalize_character_key
from app.services.shared_runtime import NARRATOR_CANONICAL_KEY, NARRATOR_DISPLAY_NAME, is_narrator_name


def _canonical_budget_character_name(value: str) -> str:
    if is_narrator_name(value):
        return NARRATOR_DISPLAY_NAME
    clean = normalize_character_key(value or "")
    clean = re.sub(r"\s+", " ", clean).strip(" -_.:,;")
    return clean


def collapse_budget_character_rows(
    characters: list[Character],
    snapshots: list[CharacterBudgetSnapshot],
) -> tuple[list[dict], dict[str, CharacterBudgetSnapshot]]:
    if not characters:
        return [], {}
    snapshot_by_character_id = {str(item.character_id): item for item in snapshots}
    collapsed: dict[str, dict] = {}
    collapsed_snapshots: dict[str, CharacterBudgetSnapshot] = {}
    for character in characters:
        char_map_id = str(getattr(character, "char_map_id", "") or "").strip()
        primary_name = str(character.name or "").strip()
        if is_narrator_name(primary_name):
            canonical = NARRATOR_DISPLAY_NAME
            primary_name = NARRATOR_DISPLAY_NAME
            aggregate_key = NARRATOR_CANONICAL_KEY
        else:
            canonical = _canonical_budget_character_name(primary_name)
            if not canonical:
                continue
            aggregate_key = f"map:{char_map_id}" if char_map_id else f"name:{canonical}"
        existing = collapsed.get(aggregate_key)
        current_snapshot = snapshot_by_character_id.get(str(character.id))
        if not existing:
            collapsed[aggregate_key] = {
                "id": character.id,
                "char_map_id": char_map_id,
                "name": str(primary_name or canonical).strip(),
                "aliases": character.aliases or "",
                "appears_in": getattr(character, "appears_in", "") or "",
                "actor_name": character.actor_name or "",
                "manual_rate_rub_per_min": character.manual_rate_rub_per_min or 0,
                "manual_fixed_rub": character.manual_fixed_rub or 0,
                "race": character.race or "",
                "temperament": character.temperament or "",
                "note": getattr(character, "operator_note", "") or "",
                "character_color": character.character_color or "",
                "character_text_color": character.character_text_color or "",
                "character_font_weight": character.character_font_weight or "",
                "character_font_style": character.character_font_style or "",
            }
            if current_snapshot:
                collapsed_snapshots[str(character.id)] = current_snapshot
            continue
        if not existing["actor_name"] and (character.actor_name or "").strip():
            existing["actor_name"] = character.actor_name or ""
        if not existing.get("aliases") and (character.aliases or "").strip():
            existing["aliases"] = character.aliases or ""
        if not existing["appears_in"] and str(getattr(character, "appears_in", "") or "").strip():
            existing["appears_in"] = getattr(character, "appears_in", "") or ""
        if not existing["race"] and (character.race or "").strip():
            existing["race"] = character.race or ""
        if not existing["temperament"] and (character.temperament or "").strip():
            existing["temperament"] = character.temperament or ""
        if not existing.get("note") and str(getattr(character, "operator_note", "") or "").strip():
            existing["note"] = getattr(character, "operator_note", "") or ""
        if not existing.get("character_color") and (character.character_color or "").strip():
            existing["character_color"] = character.character_color or ""
        if not existing.get("character_text_color") and (character.character_text_color or "").strip():
            existing["character_text_color"] = character.character_text_color or ""
        if not existing.get("character_font_weight") and (character.character_font_weight or "").strip():
            existing["character_font_weight"] = character.character_font_weight or ""
        if not existing.get("character_font_style") and (character.character_font_style or "").strip():
            existing["character_font_style"] = character.character_font_style or ""
        if not existing.get("char_map_id") and char_map_id:
            existing["char_map_id"] = char_map_id
        if not existing["manual_rate_rub_per_min"] and int(character.manual_rate_rub_per_min or 0) > 0:
            existing["manual_rate_rub_per_min"] = character.manual_rate_rub_per_min or 0
        if not existing["manual_fixed_rub"] and int(character.manual_fixed_rub or 0) > 0:
            existing["manual_fixed_rub"] = character.manual_fixed_rub or 0
        if current_snapshot:
            merged_snapshot = collapsed_snapshots.get(str(existing["id"]))
            if merged_snapshot:
                merged_snapshot.lines_count = int(merged_snapshot.lines_count or 0) + int(current_snapshot.lines_count or 0)
                merged_snapshot.approx_seconds = int(merged_snapshot.approx_seconds or 0) + int(current_snapshot.approx_seconds or 0)
                merged_snapshot.total_rub = int(merged_snapshot.total_rub or 0) + int(current_snapshot.total_rub or 0)
            else:
                collapsed_snapshots[str(existing["id"])] = current_snapshot
    rows = sorted(collapsed.values(), key=lambda item: str(item.get("name") or "").lower())
    return rows, collapsed_snapshots
