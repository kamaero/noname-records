#!/usr/bin/env python
"""Загрузить статьи авторской энциклопедии в лор.

Вторая половина того же файла, из которого импортированы карточки персонажей
(`import_author_compendium.py`): там были имена и описания сущностей, здесь —
прозаические разделы о мире и карты.

    python scripts/import_lore.py путь/к/энциклопедии.fb2 --author код_автора
    python scripts/import_lore.py … --author код_автора --apply

По умолчанию — отчёт: какие разделы возьмутся (все или выбранные --topic), сколько в них знаков и карт. Повторный
запуск с `--apply` обновляет тексты по ключу (автор, тема), а не плодит копии.
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models import Author  # noqa: E402
from app.services.lore_import import import_lore, parse_images, parse_lore_fb2, topic_filter  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="fb2 энциклопедии")
    parser.add_argument("--author", required=True, help="код автора в системе")
    parser.add_argument("--topic", action="append", default=[],
                        help="брать только этот раздел; «Мир.*» — все разделы мира (по умолчанию — все)")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    raw = open(args.path, "rb").read()
    names = {t for t in args.topic if not t.endswith("*")}
    prefixes = tuple(t[:-1] for t in args.topic if t.endswith("*"))
    sections = parse_lore_fb2(raw, wanted=topic_filter(prefixes, names))
    if not sections:
        print("Ни один раздел не прошёл отбор — проверьте --topic.", file=sys.stderr)
        return 1

    print(f"разделов взято: {len(sections)}, знаков {sum(s.chars for s in sections):,}")
    for section in sections:
        maps = f"  карт: {len(section.images)}" if section.images else ""
        print(f"  {section.chars:7,}  {section.topic}{maps}")

    with SessionLocal() as db:
        author = db.query(Author).filter(Author.slug == args.author.strip().lower()).first()
        if author is None:
            print(f"Автора «{args.author}» нет в базе", file=sys.stderr)
            return 1
        if not args.apply:
            print("\nОтчёт без записи (--apply, чтобы записать).")
            return 0
        images = parse_images(raw)
        summary = import_lore(db, author.id, sections, images, author_slug=author.slug)
        db.commit()
        print(f"\nЗаписано: новых {summary['created']}, обновлено {summary['updated']}, "
              f"карт {summary['images']}, знаков {summary['chars']:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
