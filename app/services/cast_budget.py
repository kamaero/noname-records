from __future__ import annotations

import re

DEFAULT_ACTOR_RATE_RUB_PER_MIN = 1000


def estimate_seconds_by_words(words: int) -> int:
    return int(round((words / 120) * 60))


def promote_role_into_cast(content: str, role: str, description: str = "") -> tuple[str, bool]:
    role_name = (role or "").strip()
    role_description = (description or "").strip() or "персонаж главы"
    if not role_name:
        return content, False

    cast_line_re = re.compile(r"^\[(?P<name>[^\]]+)\]\s*::")
    lines = content.splitlines()
    for idx, line in enumerate(lines):
        match = cast_line_re.match(line.strip())
        if not match:
            continue
        if match.group("name").strip() != role_name:
            continue
        current_desc = line.split("::", 1)[1].strip() if "::" in line else ""
        if role_description and current_desc != role_description:
            lines[idx] = f"[{role_name}] :: {role_description}"
            return "\n".join(lines), True
        return content, False

    new_cast_line = f"[{role_name}] :: {role_description}"
    cast_index = next((idx for idx, line in enumerate(lines) if line.strip() == "[CAST]"), -1)
    if cast_index == -1:
        body = content.strip()
        rebuilt = "[CAST]\n" + new_cast_line + ("\n\n" + body if body else "")
        return rebuilt, True

    insert_at = cast_index + 1
    while insert_at < len(lines) and cast_line_re.match(lines[insert_at].strip()):
        insert_at += 1
    lines.insert(insert_at, new_cast_line)
    return "\n".join(lines), True


def calc_character_total(lines_count: int, approx_seconds: int, character, snapshot=None) -> int:
    if character.manual_fixed_rub and character.manual_fixed_rub > 0:
        return int(character.manual_fixed_rub)
    if character.manual_rate_rub_per_min and character.manual_rate_rub_per_min > 0:
        return int(round(character.manual_rate_rub_per_min * (approx_seconds / 60.0)))
    if snapshot and snapshot.total_rub > 0:
        return int(snapshot.total_rub)
    return int(round(DEFAULT_ACTOR_RATE_RUB_PER_MIN * (max(0, approx_seconds) / 60.0)))
