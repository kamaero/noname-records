#!/usr/bin/env python
"""Загрузить авторские иллюстрации книги из fb2.

Картинки в книге не подписаны — кто на них, решает человек на экране «Иллюстрации»
(`/app/books/<id>/illustrations`). Здесь они только достаются из файла вместе с текстом
вокруг каждой, по которому экран подсказывает кандидатов.

    python scripts/import_book_images.py <fb2|zip> --book-id <id>
    python scripts/import_book_images.py <fb2|zip> --book-id <id> --apply

Перезаливка не трогает уже сделанные привязки: заново разбирается файл, а не решения.
"""
from __future__ import annotations

import argparse
import sys
import zipfile

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models import ScriptBook  # noqa: E402
from app.services.book_images import import_book_images, parse_book_images  # noqa: E402


def read_fb2(path: str) -> bytes:
    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".fb2")]
            if not names:
                raise SystemExit("в архиве нет .fb2")
            return archive.read(names[0])
    return open(path, "rb").read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    images = parse_book_images(read_fb2(args.path))
    if not images:
        print("В файле нет иллюстраций", file=sys.stderr)
        return 1
    total = sum(len(i.payload) for i in images)
    print(f"иллюстраций: {len(images)}, суммарно {total / 1024 / 1024:.1f} МБ")
    for image in images[:5]:
        print(f"  {image.key:12s} {image.chapter_hint[:40]:42s} …{image.context[:80]}…")

    with SessionLocal() as db:
        book = db.get(ScriptBook, args.book_id)
        if book is None:
            print(f"Книги {args.book_id} нет", file=sys.stderr)
            return 1
        if not args.apply:
            print("\nОтчёт без записи (--apply, чтобы записать).")
            return 0
        summary = import_book_images(
            db, book_id=book.id, author_id=str(getattr(book, "author_id", "") or ""), images=images,
        )
        db.commit()
        print(f"\nЗаписано: новых {summary['created']}, обновлено {summary['updated']}, "
              f"{summary['bytes'] / 1024 / 1024:.1f} МБ на диск")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
