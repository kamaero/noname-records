"""Import an author's compendium fb2 into the author profile.

Usage: PYTHONPATH=. .venv/bin/python3 scripts/import_author_compendium.py \
    --author код_автора --name "Имя Автора" путь/к/энциклопедии.fb2 [--topic «Раздел» ... | --all]

Без --topic/--all печатает разделы и ничего не пишет.
"""
import argparse

from app.db import SessionLocal
from app.services import author_profile
from app.services.author_imports.compendium import fb2_section_titles, import_compendium, parse_compendium_fb2, topics_from_args


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--author", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--topic", action="append", default=[], help="раздел с персонажами; «Мир.*» — приставка")
    ap.add_argument("--all", action="store_true", help="все разделы")
    args = ap.parse_args()
    raw = open(args.path, "rb").read()
    chosen = topics_from_args(raw, args.topic, args.all)
    if chosen is False:
        raise SystemExit(2)
    if chosen is None:
        entries = parse_compendium_fb2(raw)
    else:
        names, prefixes = chosen
        allow = names | {t for t, _ in fb2_section_titles(raw) if prefixes and t.startswith(prefixes)}
        entries = parse_compendium_fb2(raw, allowlist=allow)
    with SessionLocal() as db:
        author = author_profile.get_or_create_author(db, args.author, args.name or args.author)
        summary = import_compendium(db, author.id, entries)
        db.commit()
    print(f"compendium: parsed={len(entries)} {summary}")


if __name__ == "__main__":
    main()
