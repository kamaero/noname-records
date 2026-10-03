"""Put stress marks on a book's v2 segments, chapter by chapter, without a model.

The chain is the one `app.v2.stress.Resolver` defines: the author's own list first,
then the ё rule, then the dictionaries, then RUAccent for what is left. Nothing here
asks an LLM; the residue is counted and printed so a later pass knows its size.

The author layer comes from the database (`load_author_layer`: the book's own notes,
then the author's profile rows, then the imported json lists), so a stress the
author settled in the editor survives a re-run. `--author` adds one more json file
underneath all of those, for a list not yet imported.

Runs against the sqlite file named by `--db` and refuses the production one, like
the other v2 scripts. RUAccent is optional: without it the context layer is skipped
and the report says so, so a run on a machine without the models is still honest.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.v2.run import parse_chapter_range
from app.v2.stress import Resolver, author_map_from_entries, load_author_layer, stress_segment
from app.v2.store import load_chapter_segments, store_stress_marks

PROD_DB = "noname.db"
REPO = pathlib.Path(__file__).resolve().parents[2]
DEFAULT_AUTHOR = REPO / "data" / "author_stress" / "polumrak.json"


def load_author_map(path: pathlib.Path) -> dict[str, int]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return author_map_from_entries(data.get("entries") or [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("book_id")
    parser.add_argument("--db", required=True, help="путь к sqlite-файлу")
    parser.add_argument("--chapters", help="диапазон chapter_index, например 1-3,7")
    parser.add_argument("--author", default=str(DEFAULT_AUTHOR), help="json авторских ударений")
    parser.add_argument("--no-context", action="store_true", help="не использовать RUAccent")
    parser.add_argument("--i-mean-production", action="store_true")
    args = parser.parse_args()

    if os.path.realpath(args.db) == os.path.realpath(PROD_DB) and not args.i_mean_production:
        print("ОТКАЗ: это продовая база. v2 строится рядом с работающей системой, а не в ней.")
        return 2

    context = None
    if not args.no_context:
        from app.v2.stress_ruaccent import context_layer

        started = time.time()
        context = context_layer()
        print("RUAccent:", "загружен за %.0f с" % (time.time() - started) if context else "недоступен — слой пропущен")

    engine = create_engine(f"sqlite:///{args.db}")
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    from app.models import ScriptBook, ScriptChapter

    author_map = load_author_map(pathlib.Path(args.author))
    with SessionLocal() as db:
        book = db.get(ScriptBook, args.book_id)
        if book is not None:
            author_map.update(load_author_layer(db, book))
    print(f"авторский слой: {len(author_map)} слов")
    resolver = Resolver.default(author=author_map, context=context)

    wanted = parse_chapter_range(args.chapters) if args.chapters else None
    totals: dict[str, int] = {}
    unresolved: dict[str, int] = {}
    marks_total = 0

    with SessionLocal() as db:
        chapters = (
            db.query(ScriptChapter)
            .filter(ScriptChapter.book_id == args.book_id)
            .order_by(ScriptChapter.chapter_index.asc())
            .all()
        )
        chapters = [c for c in chapters if wanted is None or int(c.chapter_index) in wanted]
        if not chapters:
            print("глав не найдено")
            return 1
        print(f"глав: {len(chapters)}   база: {args.db}")

        for chapter in chapters:
            started = time.time()
            segments = load_chapter_segments(db, chapter_id=chapter.id)
            chapter_marks = 0
            for segment in segments:
                marks, report = stress_segment(segment.text, resolver=resolver)
                chapter_marks += store_stress_marks(db, segment_id=segment.id, marks=marks)
                for source, count in (report.get("counts") or {}).items():
                    totals[source] = totals.get(source, 0) + int(count)
                for word in report.get("unresolved") or []:
                    unresolved[word] = unresolved.get(word, 0) + 1
            db.commit()
            marks_total += chapter_marks
            print(f"  глава {chapter.chapter_index:>3}  сегментов {len(segments):>4}  "
                  f"ударений {chapter_marks:>6}  {time.time() - started:5.0f}с")

    print(f"\nвсего ударений: {marks_total}")
    print("по источникам:", ", ".join(f"{k} {v}" for k, v in sorted(totals.items(), key=lambda kv: -kv[1])))
    if unresolved:
        print(f"без ответа: {sum(unresolved.values())} вхождений, {len(unresolved)} слов; самые частые:")
        for word, n in sorted(unresolved.items(), key=lambda kv: -kv[1])[:15]:
            print(f"   {n:>4}  {word}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
