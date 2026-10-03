"""Пробы на роль, собранные для прослушивания.

Проба — это заявка: «я мог бы читать за него». Слушают её двое, владелец студии и
автор, и решают, чей голос ложится на роль. Диктор слушает только свои: чужая проба
не его дело, а знать, кто ещё пробовался, ему незачем.

Книга здесь названа дважды. В `audio_files` она — код, выведенный из заголовка
(«Крылья полумрака» → «КП»), потому что этот код диктор пишет в имени файла. В касте
она — идентификатор строки. Перевод между ними живёт тут, а не на экране: экран знает
книгу, которую открыл, и не должен знать, как она называется в чужой таблице.
"""
from __future__ import annotations

import re
from urllib.parse import quote

from app.models import AudioFile, ScriptBook
from app.services.audio_uploads import AUDITION, derive_book_code
from app.time_utils import iso_utc


def _row(item: AudioFile) -> dict:
    return {
        "id": str(item.id),
        "role": str(item.role or ""),
        "chapter": str(item.chapter or ""),
        "actor_name": str(item.actor_name or ""),
        "original_filename": str(item.original_filename or ""),
        "canonical_filename": str(item.canonical_filename or ""),
        "mime_type": str(item.mime_type or "audio/wav"),
        "size_bytes": int(item.size_bytes or 0),
        "uploaded_at": iso_utc(item.uploaded_at) or "",
        # Лист проб убирает от них так же, как и слушает — удаление называет цену
        # (роль, глава, длительность), а не спрашивает голое «удалить файл?».
        "duration_seconds": float(item.duration_seconds or 0.0),
    }


def book_auditions(db, *, book_id: str) -> list[dict] | None:
    """Пробы этой книги, свежие сверху. `None` — книги нет."""
    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        return None
    code = derive_book_code(str(book.title or ""))
    items = (
        db.query(AudioFile)
        .filter(AudioFile.kind == AUDITION, AudioFile.book_code == code)
        .order_by(AudioFile.uploaded_at.desc())
        .all()
    )
    return [_row(item) for item in items]


def find_audition(db, audio_id: str) -> AudioFile | None:
    """Проба по идентификатору. Дубль по тому же пути не отдаётся: это другой экран."""
    item = db.get(AudioFile, str(audio_id or "").strip())
    if item is None or str(item.kind or "") != AUDITION:
        return None
    return item


def slice_for_range(range_header: str, total: int) -> tuple[int, int] | None:
    """`bytes=400-` → (400, total-1). `None` — куска не просили или просили бессмыслицу.

    `<audio>` перематывает запросом `Range`; без ответа на него ползунок не двигается,
    и запись можно только слушать с начала.
    """
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", str(range_header or "").strip())
    if not match or total <= 0:
        return None
    raw_start, raw_end = match.group(1), match.group(2)
    if not raw_start:
        # `bytes=-500` — последние 500 байт
        if not raw_end:
            return None
        length = min(int(raw_end), total)
        return (total - length, total - 1)
    start = int(raw_start)
    if start >= total:
        return None
    end = min(int(raw_end), total - 1) if raw_end else total - 1
    if end < start:
        return None
    return (start, end)


def inline_disposition(filename: str) -> str:
    """`Content-Disposition` для файла с кириллицей в имени.

    Заголовки HTTP — latin-1, а «КП_Проба_Сатухух_Сомов.wav» — нет: имя как есть
    роняет ответ ещё до того, как его отправят. RFC 5987 — то, как имя переживает
    дорогу.
    """
    name = str(filename or "").strip() or "audition.wav"
    return f"inline; filename*=UTF-8''{quote(name)}"
