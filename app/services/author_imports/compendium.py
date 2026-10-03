from __future__ import annotations

import re

from defusedxml.ElementTree import fromstring as _xml_fromstring
from app.services import author_profile

#: Разделы энциклопедии автора, где лежат персонажи, — у каждого автора свои. Не задано —
#: берутся все разделы: молча пропустить чужую энциклопедию хуже, чем показать лишнее.
DEFAULT_PERSON_TOPICS: set[str] | None = None

_ALIAS_RE = re.compile(r"^\s*(?:Он|Она|Оно|Они)\s+же\s+(.+?)\.", re.IGNORECASE)


def _strip_fb2(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<binary\b[^>]*>.*?</binary>", "", text, flags=re.DOTALL)
    text = re.sub(r'\sxmlns(:\w+)?="[^"]*"', "", text)
    text = re.sub(r"(</?)\w+:", r"\1", text)
    text = re.sub(r"\s\w+:(\w+=)", r" \1", text)
    return text


def _title_of(section) -> str:
    title = section.find("title")
    if title is None:
        return ""
    return " ".join("".join(p.itertext()).strip() for p in title.findall(".//p"))


def parse_compendium_fb2(raw: bytes, allowlist: set[str] | None = None) -> list[dict]:
    allow = allowlist if allowlist is not None else DEFAULT_PERSON_TOPICS
    root = _xml_fromstring(_strip_fb2(raw))
    body = root.find("body")
    out: list[dict] = []
    if body is None:
        return out
    for section in body.findall("section"):
        topic = _title_of(section)
        if allow is not None and topic not in allow:
            continue
        for p in section.findall("p"):
            strong = p.find("strong")
            if strong is None or not (strong.text or "").strip():
                continue
            name = strong.text.strip().rstrip(".").strip()
            # Strip a leading list-number prefix ("1. ", "10) ") from numbered topics
            # (e.g. "Имена Мардука"), which is markup, not part of the name.
            name = re.sub(r"^\d+[.)]\s*", "", name).strip()
            if not (2 <= len(name) <= 60):
                continue
            full = "".join(p.itertext()).strip()
            rest = full[len(strong.text):].lstrip(" .—-").strip()
            aliases: list[str] = []
            m = _ALIAS_RE.match(rest)
            if m:
                aliases = [a.strip() for a in re.split(r",|;| и ", m.group(1)) if a.strip()]
            out.append({"name": name, "aliases": aliases, "topic": topic, "description": rest[:600]})
    return out


def import_compendium(db, author_id: str, entries: list[dict]) -> dict:
    summary = {"created": 0, "updated": 0}
    for e in entries:
        _, created = author_profile.upsert_character(
            db, author_id,
            canonical_name=e["name"], aliases=e.get("aliases") or [],
            description=e.get("description") or "", source_topic=e.get("topic") or "",
            status="confirmed",
        )
        summary["created" if created else "updated"] += 1
    return summary


def fb2_section_titles(raw: bytes) -> list[tuple[str, int]]:
    """Разделы энциклопедии с числом абзацев — чтобы человек сам выбрал, что брать."""
    root = _xml_fromstring(_strip_fb2(raw))
    body = root.find("body")
    if body is None:
        return []
    return [(_title_of(s), len(s.findall("p"))) for s in body.findall("section")]


def topics_from_args(raw: bytes, topics: list[str], take_all: bool):
    """(точные имена, приставки) из `--topic` (хвост «*» — приставка) или None при `--all`.

    Ни того ни другого — печатает разделы и возвращает False: импорт в чужую энциклопедию
    вслепую либо теряет статьи, либо засоряет каст заклинаниями и местами."""
    if take_all:
        return None
    if not topics:
        print("Разделы энциклопедии (абзацев):")
        for title, count in fb2_section_titles(raw):
            print(f"  {count:5}  {title or '(без названия)'}")
        print("\nВыберите: --topic «Заголовок» (можно несколько; «Мир.*» — все разделы мира) или --all.")
        return False
    names = {t for t in topics if not t.endswith("*")}
    prefixes = tuple(t[:-1] for t in topics if t.endswith("*"))
    return names, prefixes
