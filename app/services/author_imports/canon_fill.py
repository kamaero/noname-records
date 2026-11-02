"""Описания канона из разделов энциклопедии, где имя не выделено жирным (07.10).

Июньский импорт (`compendium.parse_compendium_fb2`) брал только записи с `<strong>`-именем
из списка разделов с персонажами. В других разделах автор пишет обычным абзацем «Емеммо
Шторм (в юности — Пират). Адмирал флота…», и у таких героев описание в каноне пустое —
баннер роли в Читалке был без текста, и диктор не видел, кого озвучивает.

Только для карточек, привязанных к ролям книги, и только пустые описания: заполненное
руками или импортом не перезаписываем. Запись — абзац, который НАЧИНАЕТСЯ с имени и дальше
идёт точка, скобка, запятая или тире; упоминание в середине фразы записью не считается.
Не коммитит.
"""
from __future__ import annotations

import re

from defusedxml.ElementTree import fromstring as _xml_fromstring

from app.models import AuthorCharacter, Character
from app.services.author_imports.compendium import _strip_fb2, _title_of


def _entry_rest(text: str, name: str) -> str | None:
    """Текст записи после имени, если абзац с имени начинается; иначе `None`."""
    if not text.startswith(name) or len(text) == len(name):
        return None
    after = text[len(name):]
    # После имени — точка, запятая, скобка или тире с пробелом. Пробел и слово или дефис
    # вплотную — другое, составное имя: «Хор-Ханк. Самый северный остров» не запись роли
    # «Хор» (прогон на копии прода).
    if not after.startswith((".", ",", " (", "(", " —", " -")):
        return None
    rest = text[len(name):].lstrip(" .,—-").strip()
    # «Сюкыр — упоминание…»: после имени сразу тире и строчная буква — фраза, а не запись
    if not rest or (text[len(name):].lstrip().startswith(("—", "-")) and rest[:1].islower()):
        return None
    return rest[:600]


def fill_descriptions(db, author_id: str, raw: bytes, *, book_id: str, apply: bool) -> dict:
    linked = {str(cid) for (cid,) in db.query(Character.author_character_id)
              .filter(Character.book_id == book_id, Character.author_character_id != "").all()}
    cards = [card for card in db.query(AuthorCharacter)
             .filter(AuthorCharacter.author_id == author_id, AuthorCharacter.id.in_(linked)).all()
             if not str(card.description or "").strip()]
    report = {"filled": [], "candidates": len(cards)}
    if not cards:
        return report
    root = _xml_fromstring(_strip_fb2(raw))
    body = root.find("body")
    paragraphs: list[tuple[str, str]] = []
    for section in (body.findall("section") if body is not None else []):
        topic = _title_of(section)
        for p in section.findall("p"):
            paragraphs.append((topic, " ".join("".join(p.itertext()).split())))
    for card in sorted(cards, key=lambda c: -len(c.canonical_name or "")):
        name = str(card.canonical_name or "").strip()
        if len(name) < 3:
            continue
        hit = next(((topic, rest) for topic, text in paragraphs
                    if (rest := _entry_rest(text, name)) is not None), None)
        if hit is None:
            continue
        report["filled"].append(name)
        if apply:
            card.description = hit[1]
            if not str(card.source_topic or "").strip():
                card.source_topic = hit[0]
    if apply:
        db.flush()
    return report
