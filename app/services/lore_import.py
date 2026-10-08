"""Разбор авторской энциклопедии в статьи лора.

Источник — `metauniverse.fb2`, тот же файл, из которого уже импортированы карточки
персонажей (`app/services/author_imports/compendium.py`). Оттуда берутся карточки —
«кто такой Бимькмолепус»; отсюда берётся вторая половина — прозаические статьи
разделов: как устроен Полумрак, какие в Парифате разумные виды, что такое мана.

Текстовый экспорт той же энциклопедии (`Справочные материалы.txt`) для этого не
годится: имена в нём были выделением и при конвертации пропали, поэтому карточка
барона начинается с «. Он же Темный Балаганщик…». В fb2 имя лежит в `<strong>`,
а заголовок раздела — в `<title>`.

Отбор разделов — явный: мир берётся по приставке в заголовке, общие разделы —
перечислены поимённо. Энциклопедия описывает всю вселенную (116 разделов, 2 МБ), а
дикторам «Крыльев» и «Семьи волшебников» нужны два мира и общая часть.
"""
from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
import re
from dataclasses import dataclass, field

from defusedxml.ElementTree import fromstring as _xml_fromstring

from app.services.author_imports.compendium import _strip_fb2, _title_of
from app.time_utils import utcnow_naive

_BINARY = re.compile(
    rb'<binary[^>]*content-type="(?P<type>[^"]+)"[^>]*id="(?P<id>[^"]+)"[^>]*>(?P<data>.*?)</binary>',
    re.DOTALL,
)


@dataclass
class LoreSection:
    topic: str
    body: str
    images: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.body)


def topic_filter(prefixes=(), names=()):
    """Отбор разделов энциклопедии: у каждого автора свои миры и свои заголовки.

    Без приставок и имён берётся всё, кроме разделов без названия (статья без названия
    не находится). С ними — разделы, чей заголовок начинается с приставки («Полумрак.»)
    или совпадает с именем целиком («Нежить.»): так из энциклопедии целой вселенной
    берут только миры этой книги."""
    prefixes, names = tuple(prefixes), set(names)

    def wanted(title: str) -> bool:
        name = " ".join(str(title or "").split())
        if not name:
            return False
        if not prefixes and not names:
            return True
        return name.startswith(prefixes) or name in names
    return wanted


topic_wanted = topic_filter()


def parse_images(raw: bytes) -> dict[str, tuple[str, bytes]]:
    """Картинки файла: ключ → (тип, байты). Разбираются по сырому тексту.

    `_strip_fb2` вырезает `<binary>` до разбора XML — иначе 21 МБ base64 прошли бы
    через дерево впустую. Поэтому картинки достаются отдельно, регулярным выражением.
    """
    out: dict[str, tuple[str, bytes]] = {}
    for match in _BINARY.finditer(raw):
        key = match.group("id").decode("utf-8", errors="replace").strip()
        content_type = match.group("type").decode("utf-8", errors="replace").strip()
        if not key:
            continue
        try:
            out[key] = (content_type, base64.b64decode(match.group("data"), validate=False))
        except (binascii.Error, ValueError):
            # Битая картинка не должна ронять импорт текста: статья полезна и без карты.
            continue
    return out


def _section_images(section) -> list[str]:
    """Ссылки на картинки раздела — и прямые, и внутри абзацев."""
    keys = []
    for node in section.iter("image"):
        href = str(node.get("href") or "").strip().lstrip("#")
        if href and href not in keys:
            keys.append(href)
    return keys


def _section_body(section) -> str:
    """Абзацы раздела одной строкой с пустой строкой между ними."""
    parts = []
    for node in section.findall("p"):
        text = " ".join("".join(node.itertext()).split())
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def parse_lore_fb2(raw: bytes, *, wanted=topic_wanted) -> list[LoreSection]:
    """Разделы энциклопедии, прошедшие отбор, в порядке файла."""
    root = _xml_fromstring(_strip_fb2(raw))
    body = root.find("body")
    if body is None:
        return []
    out: list[LoreSection] = []
    for section in body.findall("section"):
        topic = " ".join(_title_of(section).split())
        if not wanted(topic):
            continue
        text = _section_body(section)
        if not text:
            # Раздел из одной картинки (карта без подписи) — не статья, но и не потеря:
            # карта попадёт в статью того же мира, где текст есть.
            continue
        out.append(LoreSection(topic=topic, body=text, images=_section_images(section)))
    return out


#: корень для картинок энциклопедии — рядом с прочими данными книги, вне репозитория
def lore_image_root() -> Path:
    from app.paths import data_path
    return data_path("lore")


def image_dir(author_slug: str, root: str | None = None) -> str:
    return os.path.join(root or str(lore_image_root()), re.sub(r"[^a-zA-Z0-9_-]+", "-", str(author_slug or "author")))


def import_lore(db, author_id: str, sections: list[LoreSection],
                images: dict[str, tuple[str, bytes]], *, author_slug: str,
                root: str | None = None) -> dict:
    """Записать статьи и их картинки. Ключ обновления — (автор, тема).

    Повторный запуск обновляет тексты и порядок, а не плодит копии: энциклопедия
    прирастает новыми разделами, и перезалить её должно быть дёшево.
    """
    from app.models import LoreArticle, LoreImage

    wanted_keys = {key for section in sections for key in section.images}
    saved_images = 0
    if wanted_keys:
        folder = image_dir(author_slug, root)
        os.makedirs(folder, exist_ok=True)
        for key in sorted(wanted_keys):
            found = images.get(key)
            if not found:
                continue
            content_type, payload = found
            path = os.path.join(folder, re.sub(r"[^a-zA-Z0-9._-]+", "-", key))
            with open(path, "wb") as handle:
                handle.write(payload)
            row = (
                db.query(LoreImage)
                .filter(LoreImage.author_id == author_id, LoreImage.image_key == key)
                .first()
            )
            if row is None:
                row = LoreImage(author_id=author_id, image_key=key)
                db.add(row)
            row.content_type = content_type or "image/jpeg"
            row.stored_path = path
            row.bytes_len = len(payload)
            saved_images += 1

    created = updated = 0
    for ordinal, section in enumerate(sections):
        row = (
            db.query(LoreArticle)
            .filter(LoreArticle.author_id == author_id, LoreArticle.topic == section.topic)
            .first()
        )
        if row is None:
            row = LoreArticle(author_id=author_id, topic=section.topic)
            db.add(row)
            created += 1
        else:
            updated += 1
        row.title = section.topic.rstrip(".")
        row.body = section.body
        row.image_keys = ",".join(section.images)
        row.ordinal = ordinal
        row.updated_at = utcnow_naive()
    return {"created": created, "updated": updated, "images": saved_images,
            "chars": sum(s.chars for s in sections)}
