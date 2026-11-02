"""Починка карточек канона, испорченных июньским разбором энциклопедии (06.10).

Старый разбор резал описание от начала абзаца по длине имени. Перед именем бывает номер
(«1513. <strong>Ныкфо Пудисди</strong>»), и описание начиналось с обрезка — «удисди.
Волшебник…»; имя, разбитое на два выделения («Ныкфо П» + «эыхялсы»), оставалось обрезанным.
Разбор исправлен в `compendium._name_and_rest`; здесь — починка того, что уже лежит в базе.

Перезаписываем только то, что лежит ровно в испорченном виде (совпадает с выдачей старого
разбора): карточку, которую потом правили руками, не трогаем. Обрезанное имя: есть
карточка с полным именем — переносим в неё описание и удаляем обрезанную (если на неё никто
не ссылается); нет — переименовываем. Не коммитит.
"""
from __future__ import annotations

import json
import re

from defusedxml.ElementTree import fromstring as _xml_fromstring

from app.models import AuthorCharacter, BookIllustration, Character, Recast
from app.services.author_imports.compendium import (
    DEFAULT_PERSON_TOPICS, _ALIAS_RE, _name_and_rest, _strip_fb2, _title_of,
)

_NUMBER = re.compile(r"^\d+[.)]\s*")


def _legacy(p) -> tuple[str, str]:
    """Имя и описание так, как их выдавал разбор до 06.10 — чтобы узнать испорченное."""
    strong = p.find("strong")
    if strong is None or not (strong.text or "").strip():
        return "", ""
    name = _NUMBER.sub("", strong.text.strip().rstrip(".").strip()).strip()
    full = "".join(p.itertext()).strip()
    return name, full[len(strong.text):].lstrip(" .—-").strip()[:600]


def _aliases(rest: str) -> list[str]:
    match = _ALIAS_RE.match(rest)
    return [a.strip() for a in re.split(r",|;| и ", match.group(1)) if a.strip()] if match else []


def _referenced(db, character_id: str) -> bool:
    return any(db.query(model).filter(column == character_id).first() is not None for model, column in (
        (Character, Character.author_character_id),
        (Recast, Recast.author_character_id),
        (BookIllustration, BookIllustration.character_id),
    ))


def repair(db, author_id: str, raw: bytes, *, apply: bool) -> dict:
    root = _xml_fromstring(_strip_fb2(raw))
    body = root.find("body")
    report = {"descriptions": [], "renamed": [], "merged": [], "kept_referenced": [], "skipped_edited": []}
    if body is None:
        return report
    cards = {c.canonical_name: c for c in db.query(AuthorCharacter).filter(AuthorCharacter.author_id == author_id)}
    seen: set[str] = set()
    for section in body.findall("section"):
        topic = _title_of(section)
        if DEFAULT_PERSON_TOPICS is not None and topic not in DEFAULT_PERSON_TOPICS:
            continue
        for p in section.findall("p"):
            old_name, old_rest = _legacy(p)
            new_name, new_rest = _name_and_rest(p)
            new_name = _NUMBER.sub("", new_name).strip()
            new_rest = new_rest[:600]
            if not old_name or old_name in seen or (old_name, old_rest) == (new_name, new_rest):
                continue
            card = cards.get(old_name)
            if card is None or card.source_topic != topic:
                continue
            seen.add(old_name)  # одно имя в разделе дважды — карточка одна, взята по первому
            if card.description != old_rest:
                report["skipped_edited"].append(old_name)
                continue
            if new_name == old_name:
                report["descriptions"].append(old_name)
                if apply:
                    card.description = new_rest
                    # «три. Он же Молчальник…» — за обрезком старый разбор не узнал псевдоним
                    if card.aliases in ("", "[]"):
                        card.aliases = json.dumps(_aliases(new_rest), ensure_ascii=False)
                continue
            full = cards.get(new_name)
            if full is None:
                report["renamed"].append(f"{old_name} → {new_name}")
                if apply:
                    card.canonical_name, card.description = new_name, new_rest
                    if card.aliases in ("", "[]"):
                        card.aliases = json.dumps(_aliases(new_rest), ensure_ascii=False)
                continue
            if _referenced(db, card.id):
                # на обрезанную ссылаются роль, рекаст или портрет — не удаляем, только чиним текст
                report["kept_referenced"].append(old_name)
                if apply:
                    card.description = new_rest
                continue
            report["merged"].append(f"{old_name} → {new_name}")
            if apply:
                if not full.description:
                    full.description = new_rest
                if not full.source_topic:
                    full.source_topic = topic
                db.delete(card)
    if apply:
        db.flush()
    return report
