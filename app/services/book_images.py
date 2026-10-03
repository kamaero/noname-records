"""Иллюстрации книги: достать из fb2 и понять, кого предлагать в подписи.

В fb2 «Крыльев Полумрака» 190 авторских иллюстраций, и ни одна не подписана — это не
портретные вклейки, а картинки по ходу сцен. Кто на них, видно только из текста вокруг,
поэтому каждая строка хранит свой контекст, а кандидатов подсказывает поиск имён каста
в этом контексте. Решает человек: экран привязки предлагает, но не ставит.

Кандидаты считаются на лету, а не пишутся в базу: каст книги меняется (роль
переименовали, алиас добавили), и подсказка, замороженная при загрузке, начала бы врать.
"""
from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass

from app.services.author_profile import normalize_name
from app.v2.cast_ops import split_aliases

#: сколько знаков текста до и после картинки держать как контекст
CONTEXT_BEFORE = 900
CONTEXT_AFTER = 400
#: слово короче этого совпадает со всем подряд («Мать», «Зло»), в кандидаты не идёт
MIN_NAME_KEY = 4
#: сколько кандидатов показывать кнопками
TOP_CANDIDATES = 6

_BINARY = re.compile(
    rb'<binary[^>]*id="(?P<id>[^"]+)"[^>]*content-type="(?P<type>[^"]+)"[^>]*>(?P<data>.*?)</binary>'
    rb'|<binary[^>]*content-type="(?P<type2>[^"]+)"[^>]*id="(?P<id2>[^"]+)"[^>]*>(?P<data2>.*?)</binary>',
    re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")


def sniff_image_type(head: bytes) -> str:
    """Формат картинки по её первым байтам; пусто — не картинка, которую можно отдать.

    Тип из fb2 не годится ни как защита, ни как правда: `text/html` из чужого файла
    исполнился бы на нашем домене, а у Белозёрова все картинки подписаны нестандартным
    `image/jpg`, хотя внутри JPEG, PNG и WebP вперемешку. SVG сюда не входит нарочно —
    это документ со скриптами, а не картинка.
    """
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return ""


def image_response(path: str):
    """Отдать картинку из книги с типом, который показывают её байты, а не её подпись."""
    from fastapi.responses import FileResponse

    with open(path, "rb") as handle:
        media_type = sniff_image_type(handle.read(12)) or "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"})


@dataclass
class BookImage:
    key: str
    ordinal: int
    context: str
    chapter_hint: str
    content_type: str
    payload: bytes


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", _TAGS.sub(" ", text)).strip()


def _binaries(raw: bytes) -> dict[str, tuple[str, bytes]]:
    """Картинки файла по сырому тексту: порядок атрибутов в fb2 бывает любым."""
    out: dict[str, tuple[str, bytes]] = {}
    for match in _BINARY.finditer(raw):
        key = (match.group("id") or match.group("id2") or b"").decode("utf-8", errors="replace").strip()
        content_type = (match.group("type") or match.group("type2") or b"").decode("utf-8", errors="replace").strip()
        data = match.group("data") if match.group("data") is not None else match.group("data2")
        if not key or data is None:
            continue
        try:
            out[key] = (content_type or "image/jpeg", base64.b64decode(data, validate=False))
        except (binascii.Error, ValueError):
            continue
    return out


def parse_book_images(raw: bytes) -> list[BookImage]:
    """Иллюстрации в порядке книги, с текстом вокруг каждой.

    Обложка пропускается: это не иллюстрация к сцене и ничьим портретом не станет.
    """
    images = _binaries(raw)
    text = raw.decode("utf-8", errors="replace")
    body = re.sub(r"<binary\b[^>]*>.*?</binary>", "", text, flags=re.DOTALL)
    # Служебный блок fb2 (аннотация, автор, id) стоит перед первой главой, и без этого
    # контекст первой картинки начинался бы с «author.today 126111 2026-02-07».
    body = re.sub(r"<description\b.*?</description>", "", body, flags=re.DOTALL)
    out: list[BookImage] = []
    for ordinal, match in enumerate(re.finditer(r'<image[^>]*href="#([^"]+)"[^>]*>', body), start=1):
        key = match.group(1).strip()
        if key.lower().startswith("cover"):
            continue
        found = images.get(key)
        if not found:
            continue
        # Окно режет разметку где придётся: хвост тега в начале («p>») и его голова в
        # конце («<emph») — не текст, и _clean их не узнал бы.
        before = re.sub(r"^[^<]*?>", "", body[max(0, match.start() - CONTEXT_BEFORE):match.start()])
        after = re.sub(r"<[^>]*$", "", body[match.end():match.end() + CONTEXT_AFTER])
        titles = re.findall(r"<title>(.*?)</title>", body[:match.start()], re.DOTALL)
        out.append(BookImage(
            key=key,
            ordinal=ordinal,
            context=(_clean(before) + " ⟦здесь картинка⟧ " + _clean(after)).strip(),
            chapter_hint=_clean(titles[-1])[:200] if titles else "",
            content_type=found[0],
            payload=found[1],
        ))
    return out


def image_dir(book_id: str, root: str = "data/lore") -> str:
    return os.path.join(root, "books", re.sub(r"[^a-zA-Z0-9_-]+", "-", str(book_id or "book")))


def import_book_images(db, *, book_id: str, author_id: str, images: list[BookImage],
                       root: str = "data/lore") -> dict:
    """Записать иллюстрации книги. Ключ обновления — (книга, имя картинки).

    Привязку, сделанную человеком, перезаливка не трогает: заново разбирают файл, а не
    решения.
    """
    from app.models import BookIllustration

    folder = image_dir(book_id, root)
    os.makedirs(folder, exist_ok=True)
    created = updated = 0
    for image in images:
        path = os.path.join(folder, re.sub(r"[^a-zA-Z0-9._-]+", "-", image.key))
        with open(path, "wb") as handle:
            handle.write(image.payload)
        row = (
            db.query(BookIllustration)
            .filter(BookIllustration.book_id == book_id, BookIllustration.image_key == image.key)
            .first()
        )
        if row is None:
            row = BookIllustration(book_id=book_id, image_key=image.key)
            db.add(row)
            created += 1
        else:
            updated += 1
        row.author_id = author_id
        row.ordinal = image.ordinal
        row.stored_path = path
        row.content_type = image.content_type
        row.bytes_len = len(image.payload)
        row.context = image.context
        row.chapter_hint = image.chapter_hint
    return {"created": created, "updated": updated,
            "bytes": sum(len(i.payload) for i in images)}


def cast_index(db, book_id: str) -> list[tuple[str, str, str]]:
    """(ключ написания, имя роли, id канон-карточки) — по чему искать в контексте.

    Берутся имя роли и все её алиасы: в тексте персонажа зовут и «Дгарнин», и «Дгар».
    Роль без канон-карточки в подсказки не идёт — привязывать портрет не к чему.
    """
    from app.models import Character

    out: list[tuple[str, str, str]] = []
    for row in db.query(Character).filter(Character.book_id == book_id).all():
        canon_id = str(getattr(row, "author_character_id", "") or "")
        if not canon_id:
            continue
        name = str(row.name or "").strip()
        for form in [name, *split_aliases(str(row.aliases or ""))]:
            key = normalize_name(form)
            if len(key) >= MIN_NAME_KEY:
                out.append((key, name, canon_id))
    return out


def candidates(context: str, index: list[tuple[str, str, str]], *, limit: int = TOP_CANDIDATES) -> list[dict]:
    """Кого текст вокруг картинки называет чаще прочих.

    Имя ищется с начала слова, а конец свободен: книга склоняет («Дгарнина», «Погтду»),
    зато «мать» не найдётся внутри «обнимать». Упоминание — это место в тексте, а не
    совпавшее написание: «Дгар» и «Дгарнин» с одной позиции — одно упоминание.
    """
    folded = normalize_name(context)
    if not folded:
        return []
    names: dict[str, str] = {}
    places: dict[str, set[int]] = {}
    for key, name, canon_id in index:
        found = {m.start() for m in re.finditer(r"(?<!\w)" + re.escape(key), folded)}
        if found:
            names.setdefault(canon_id, name)
            places.setdefault(canon_id, set()).update(found)
    hits = [{"character_id": cid, "name": names[cid], "hits": len(spots)} for cid, spots in places.items()]
    return sorted(hits, key=lambda item: (-item["hits"], item["name"]))[:limit]
