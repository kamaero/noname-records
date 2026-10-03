from __future__ import annotations

import html as html_lib
import re
from app.services import author_profile


def parse_legend_html(html: str) -> dict[str, str]:
    names = re.findall(r">([А-ЯЁ][^<>]{1,60}?)<", html)
    colors = re.findall(r"background-color:\s*(#[0-9a-fA-F]{3,6})", html)
    out: dict[str, str] = {}
    for name, color in zip(names, colors):
        n = html_lib.unescape(name).strip()
        low = n.lower()
        if n and not low.startswith(("легенда", "персонаж")) and n not in out:
            out[n] = color
    return out


def import_legend(db, author_id: str, name_to_color: dict[str, str]) -> dict:
    summary = {"color_set": 0, "created_unconfirmed": 0, "conflicts": [], "noop": 0}
    for name, color in name_to_color.items():
        ch = author_profile.find_character(db, author_id, name)
        if ch is None:
            ch, _ = author_profile.upsert_character(
                db, author_id, canonical_name=name, aliases=[], status="unconfirmed",
            )
            ch.reply_color = color.strip() if color else ""
            summary["created_unconfirmed"] += 1
            continue
        result = author_profile.set_color(ch, color)
        if result == "set":
            summary["color_set"] += 1
        elif result == "conflict":
            summary["conflicts"].append({"character": ch.canonical_name, "existing": ch.reply_color, "incoming": color})
        else:
            summary["noop"] += 1
    return summary
