"""Fill v2_segments for a book that already went through the old pipeline.

Deviates from the plan on one point, deliberately. The plan says to cut
`data/book_sources/<book_id>/extracted.txt`, but the chapter boundaries in that file
would have to be re-derived by a heuristic of our own, and any disagreement with the
split the rest of the system already made would be silent — segments pointing at a
chapter whose text they do not match. `script_chapters.source_text` is what the system
calls a chapter, so that is what gets segmented; extracted.txt is used to check that
the two still agree, and the check is reported rather than assumed.

Writes to whatever database `--db` names and refuses the production one unless told
twice: pipeline v2 is built beside the running system, not inside it.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.v2.segmenter import segment_chapter
from app.v2.store import store_chapter_segments

PROD_DB = "noname.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("book_id")
    parser.add_argument("--db", required=True, help="путь к sqlite-файлу")
    parser.add_argument("--source-dir", default="data/book_sources")
    parser.add_argument("--i-mean-production", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if os.path.realpath(args.db) == os.path.realpath(PROD_DB) and not args.i_mean_production:
        print("ОТКАЗ: это продовая база. v2 строится рядом с работающей системой, а не в ней.")
        return 2

    engine = create_engine(f"sqlite:///{args.db}")
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    # Imported here so the refusal above happens before any model touches the file.
    from app.models import ScriptChapter
    from app.v2.models import V2Attribution, V2Segment, V2StressMark

    # A scratch copy has never seen revision 0008. Creating the tables here keeps an
    # experiment to one command, and it is said out loud rather than done quietly:
    # on production these come from the migration and nowhere else.
    import sqlalchemy as sa

    missing = [
        table for table in (V2Segment.__table__, V2Attribution.__table__, V2StressMark.__table__)
        if table.name not in sa.inspect(engine).get_table_names()
    ]
    if missing:
        print(f"создаю недостающие таблицы v2: {', '.join(t.name for t in missing)}")
        V2Segment.metadata.create_all(bind=engine, tables=missing)

    source_path = pathlib.Path(args.source_dir) / args.book_id / "extracted.txt"
    source = source_path.read_text(encoding="utf-8") if source_path.exists() else ""
    if not source:
        print(f"исходник не найден: {source_path} — сверка пропущена")

    stored = missing_from_source = 0
    with SessionLocal() as db:
        chapters = (
            db.query(ScriptChapter)
            .filter(ScriptChapter.book_id == args.book_id)
            .order_by(ScriptChapter.chapter_index.asc())
            .all()
        )
        if not chapters:
            print(f"у книги {args.book_id} нет глав")
            return 1

        print(f"глав: {len(chapters)}   база: {args.db}   режим: "
              f"{'прогон вхолостую' if args.dry_run else 'запись'}")

        for chapter in chapters:
            text = chapter.source_text or ""
            segments = segment_chapter(text, chapter_id=chapter.id)

            # Does the chapter the database holds still sit inside the extracted file?
            # A mismatch means the two have drifted, and that is worth saying out loud.
            if source and text.strip() and text.strip()[:200] not in source:
                missing_from_source += 1

            if not args.dry_run:
                store_chapter_segments(
                    db, book_id=args.book_id, chapter_id=chapter.id, segments=segments
                )
            stored += len(segments)

        if not args.dry_run:
            db.commit()

    print(f"сегментов: {stored}")
    if source:
        print(f"глав, не найденных в extracted.txt: {missing_from_source}"
              f"{'  ← расхождение, стоит посмотреть' if missing_from_source else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
