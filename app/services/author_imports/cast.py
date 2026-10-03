from __future__ import annotations

import json
import re
from app.services import author_profile


def parse_cast_html(html: str) -> dict[str, str]:
    m = re.search(r"const\s+CAST_DATA\s*=\s*(\[.*?\])\s*;", html, re.DOTALL)
    if not m:
        return {}
    role_to_actor: dict[str, str] = {}
    for entry in json.loads(m.group(1)):
        actor = str(entry.get("actor") or "").strip()
        for role in entry.get("roles") or []:
            key = str(role).strip()
            if key and key not in role_to_actor:
                role_to_actor[key] = actor
    return role_to_actor


def import_cast(db, author_id: str, role_to_actor: dict[str, str]) -> dict:
    summary = {"actor_set": 0, "created_unconfirmed": 0, "conflicts": [], "noop": 0}
    for role, actor in role_to_actor.items():
        ch = author_profile.find_character(db, author_id, role)
        if ch is None:
            ch, _ = author_profile.upsert_character(
                db, author_id, canonical_name=role, aliases=[], status="unconfirmed",
            )
            ch.actor_name = actor.strip() if actor else ""
            summary["created_unconfirmed"] += 1
            continue
        result = author_profile.set_actor(ch, actor)
        if result == "set":
            summary["actor_set"] += 1
        elif result == "conflict":
            summary["conflicts"].append({"character": ch.canonical_name, "existing": ch.actor_name, "incoming": actor})
        else:
            summary["noop"] += 1
    return summary
