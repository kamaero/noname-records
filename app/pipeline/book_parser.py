"""
Book file decoders and filename normalisation utilities.
Handles .fb2, .epub, .docx, .txt formats; no DB imports.
"""
from __future__ import annotations

import io
import logging
import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from typing import Iterable

from ebooklib import ITEM_DOCUMENT, epub

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Filename transliteration tables
# ---------------------------------------------------------------------------

AUTHOR_TOKEN_MAP = {
    "belozerovi": "Белозёровы",
    "belozerov": "Белозёров",
    "belozerova": "Белозёрова",
    "стругацкие": "Стругацкие",
    "strugatskie": "Стругацкие",
    "strugackie": "Стругацкие",
}
TRANSLIT_PAIRS = [
    ("shch", "щ"),
    ("yo", "ё"),
    ("zh", "ж"),
    ("kh", "х"),
    ("ts", "ц"),
    ("ch", "ч"),
    ("sh", "ш"),
    ("yu", "ю"),
    ("ya", "я"),
    ("ye", "е"),
]
TRANSLIT_CHARS = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и",
    "j": "й", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р",
    "s": "с", "t": "т", "u": "у", "v": "в", "w": "в", "x": "кс", "y": "ы", "z": "з",
}
FILENAME_TOKEN_OVERRIDES = {
    # Common translit edge-case in filenames: "semya" should become "семья", not "семя".
    "semya": "семья",
}


# ---------------------------------------------------------------------------
# Binary file decoders
# ---------------------------------------------------------------------------

def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1251"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="ignore")


def _extract_fb2_text(payload: bytes) -> str:
    chunks: list[str] = []
    try:
        stream = io.BytesIO(payload)
        for event, elem in ET.iterparse(stream, events=("end",)):
            tag = (elem.tag or "").lower()
            if tag.endswith(("title", "subtitle", "p", "v")):
                text = " ".join(t.strip() for t in elem.itertext() if t and t.strip())
                if text:
                    chunks.append(text)
            elif tag.endswith("empty-line"):
                chunks.append("")
            elem.clear()
        text = "\n\n".join(chunks)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        if text.strip():
            return text.strip()
    except Exception:
        logger.debug("Structured book parse failed; falling back to plain-text decode", exc_info=True)
    text = _decode_text(payload)
    text = re.sub(r"</(p|section|title|subtitle|v|empty-line)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_fb2_author(payload: bytes) -> str:
    """Best-effort author from fb2 ``<description><title-info><author>``.

    Reads only the file head — title-info sits before the body and the (huge)
    embedded image binaries — so this is safe and fast on multi-MB image-heavy fb2.
    Returns "First [Middle] Last" (or nickname), else "".
    """
    head = _decode_text(payload[:65536])
    ti = re.search(r"<title-info>(.*?)</title-info>", head, re.S | re.I)
    block = ti.group(1) if ti else head
    am = re.search(r"<author>(.*?)</author>", block, re.S | re.I)
    if not am:
        return ""
    author = am.group(1)

    def _tag(name: str) -> str:
        m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", author, re.S | re.I)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    parts = [p for p in (_tag("first-name"), _tag("middle-name"), _tag("last-name")) if p]
    if not parts:
        nick = _tag("nickname")
        parts = [nick] if nick else []
    return " ".join(parts)


def _extract_epub_text(payload: bytes) -> str:
    chunks: list[str] = []
    try:
        with tempfile.NamedTemporaryFile(suffix=".epub") as tmp:
            tmp.write(payload)
            tmp.flush()
            book = epub.read_epub(tmp.name)
        docs: Iterable = book.get_items_of_type(ITEM_DOCUMENT)
        for item in docs:
            try:
                content = item.get_content().decode("utf-8", errors="ignore")
            except Exception:
                continue
            content = re.sub(r"</(p|section|h1|h2|h3|h4|li|div)>", "\n", content, flags=re.IGNORECASE)
            content = re.sub(r"<[^>]+>", " ", content)
            content = re.sub(r"[ \t]+", " ", content)
            content = re.sub(r"\n{3,}", "\n\n", content).strip()
            if content:
                chunks.append(content)
    except Exception as exc:
        logger.warning("EPUB parse via ebooklib failed, fallback to zip scan: %s", exc)
        chunks = []
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                text_names = sorted(
                    name for name in zf.namelist()
                    if name.lower().endswith((".xhtml", ".html", ".htm"))
                )
                for name in text_names:
                    try:
                        raw = zf.read(name)
                    except KeyError:
                        continue
                    content = _decode_text(raw)
                    content = re.sub(r"</(p|section|h1|h2|h3|h4|li|div|br)>", "\n", content, flags=re.IGNORECASE)
                    content = re.sub(r"<[^>]+>", " ", content)
                    content = re.sub(r"[ \t]+", " ", content)
                    content = re.sub(r"\n{3,}", "\n\n", content).strip()
                    if content:
                        chunks.append(content)
        except Exception:
            logger.error("EPUB fallback zip scan failed", exc_info=True)
            chunks = []
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Filename normalisation utilities
# ---------------------------------------------------------------------------

def _translit_filename_token(token: str) -> str:
    raw = (token or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        return raw
    lower = raw.lower()
    if lower in FILENAME_TOKEN_OVERRIDES:
        return FILENAME_TOKEN_OVERRIDES[lower]
    for src, dst in TRANSLIT_PAIRS:
        lower = lower.replace(src, dst)
    out = "".join(TRANSLIT_CHARS.get(ch, ch) for ch in lower)
    return out


def _normalize_filename_words(tokens: list[str]) -> str:
    words = [_translit_filename_token(token) for token in tokens if token]
    joined = " ".join(word for word in words if word).strip()
    if not joined:
        return ""
    return joined[:1].upper() + joined[1:].lower()


def _strip_service_tokens(parts: list[str]) -> list[str]:
    service = {"fb2", "epub", "docx", "txt", "book", "kniga", "roman", "cycle", "seriya", "series", "vol", "volume", "tom"}
    return [part for part in parts if part.lower() not in service]


def parse_filename_title(filename: str) -> tuple[str, str]:
    """Разобрать имя файла на автора и название: («Белозёровы», «Сказки волшебников 2»).

    Автора отдаём отдельно, а не приклеиваем к названию: из названия выводится код
    книги для имён файлов актёров (`app.services.audio_uploads.derive_book_code`),
    и автор в нём даёт чужой код. Витрину собирает `app.services.book_title`.
    """
    stem = re.sub(r"\.[^.]+$", "", filename).strip()
    if not stem:
        return "", "Без названия"
    parts = _strip_service_tokens([part for part in re.split(r"[_\-\s]+", stem) if part])
    if not parts:
        return "", stem
    author = AUTHOR_TOKEN_MAP.get(parts[0].lower(), "")
    volume = ""
    body_tokens = parts
    if author:
        body_tokens = parts[1:]
    if body_tokens and body_tokens[-1].isdigit():
        volume = body_tokens[-1]
        body_tokens = body_tokens[:-1]
    body = _normalize_filename_words(body_tokens)
    if author and body and volume:
        return author, f"{body} {volume}"
    if author and body:
        return author, body
    if body and volume:
        return "", f"{body} Том {volume}"
    # От имени файла осталось одно лишь имя автора: это и есть всё название, какое
    # есть, — иначе получилось бы «Белозёровы - "Белозёровы"».
    return "", (_normalize_filename_words(parts) or stem)


def _display_title_from_filename(filename: str) -> str:
    """Витрина прямо из имени файла — «Автор - "Название"», когда автор узнан."""
    from app.services.book_title import compose_display_title

    author, title = parse_filename_title(filename)
    return compose_display_title(author, title)


def _title_from_filename(filename: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", filename).strip()
    return stem or "Без названия"
