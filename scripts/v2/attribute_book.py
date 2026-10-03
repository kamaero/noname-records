"""Attribute a book's v2 segments and store the answers as versioned annotations.

Same two passes as the ground-truth runner, fed from the database instead of docx:
units come from `v2_segments`, the cast from the book's `characters` (name plus
aliases, narrowed to those the old pipeline saw in the chapter when it recorded
that), and the results go to `v2_attributions` as the next version for each segment,
with the model's token use logged under `stage='v2_attribution'`.

Headings are not asked about. A chapter title is read by the narrator, always, and a
model call about it would be a call spent on a question with one answer.

Refuses the production database unless told twice, like `segment_book.py`: v2 is
built beside the running system, not inside it.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.config import settings
# The chapter-level pieces live in `app.v2.pipeline` so the worker and this script
# attribute a chapter the same way; the script only decides which chapters and how many threads.
from app.v2.pipeline import HEADING_SOURCE, cast_for_chapter, heading_records  # noqa: F401
from app.v2.run import attribute_chapter, merge_stats, new_stats, parse_chapter_range

PROD_DB = "noname.db"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("book_id")
    parser.add_argument("--db", required=True, help="путь к sqlite-файлу")
    parser.add_argument("--chapters", help="по chapter_index: «1-3», «1,4,7-9»")
    parser.add_argument("--provider", default=settings.default_final_provider)
    parser.add_argument("--model", default=settings.default_final_model)
    parser.add_argument("--thinking", choices=("on", "off"), default="on", help="режим размышлений модели")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--no-review", action="store_true", help="без второго прохода по слабым")
    parser.add_argument("--limit-paragraphs", type=int, default=0, help="для дешёвой пробы: первые N сегментов главы")
    parser.add_argument("--i-mean-production", action="store_true")
    args = parser.parse_args()

    if os.path.realpath(args.db) == os.path.realpath(PROD_DB) and not args.i_mean_production:
        print("ОТКАЗ: это продовая база. v2 строится рядом с работающей системой, а не в ней.")
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Character, LlmUsageLog, ScriptBook, ScriptChapter
    from app.v2.llm import make_llm
    from app.v2.store import load_chapter_segments, store_attributions
    from app.v2.units import units_from_segments

    engine = create_engine(f"sqlite:///{args.db}")
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    chosen = parse_chapter_range(args.chapters)
    limit = args.limit_paragraphs or None
    extra_body = {"thinking": {"type": "disabled"}} if args.thinking == "off" else None
    llm = make_llm(args.provider, args.model, extra_body=extra_body)

    with SessionLocal() as db:
        book = db.get(ScriptBook, args.book_id)
        if book is None:
            print(f"книги {args.book_id} нет в {args.db}")
            return 1
        chapters = (
            db.query(ScriptChapter)
            .filter(ScriptChapter.book_id == args.book_id)
            .order_by(ScriptChapter.chapter_index.asc())
            .all()
        )
        chapters = [c for c in chapters if chosen is None or c.chapter_index in chosen]
        if not chapters:
            print("глав не выбрано — проверь --chapters")
            return 1
        characters = db.query(Character).filter(Character.book_id == args.book_id).all()

        # Everything a thread needs is read here, on the main thread; sessions are not
        # shared across threads, and neither are the rows they hand out.
        jobs = []
        for chapter in chapters:
            segments = load_chapter_segments(db, chapter_id=chapter.id)
            if not segments:
                print(f"  глава {chapter.chapter_index}: нет v2_segments — сначала segment_book.py")
                continue
            units = units_from_segments(segments)
            if limit:
                units = units[:limit]
            heading_ids = {s.id for s in segments if s.kind == "heading"}
            cast, cast_lines = cast_for_chapter(characters, chapter.chapter_index)
            jobs.append({
                "chapter_id": chapter.id,
                "chapter_index": chapter.chapter_index,
                "title": chapter.chapter_title,
                "headings": [u for u in units if u.id in heading_ids],
                "units": [u for u in units if u.id not in heading_ids],
                "cast": cast,
                "cast_lines": cast_lines,
            })
        if not jobs:
            return 1

        print(f"книга: {book.title}   глав: {len(jobs)}   персонажей: {len(characters)}   "
              f"модель: {args.provider}/{args.model}   потоков: {args.workers}   "
              f"пересмотр: {'нет' if args.no_review else 'да'}")

        def run(job: dict) -> dict:
            started = time.monotonic()
            records, problems, stats = attribute_chapter(
                job["units"], cast=job["cast"], cast_lines=job["cast_lines"], llm=llm,
                review=not args.no_review,
            )
            return {**job, "records": heading_records(job["headings"]) + records,
                    "problems": problems, "stats": stats, "elapsed": time.monotonic() - started}

        totals = new_stats()
        all_problems: list[str] = []
        stored_total = 0
        started = time.monotonic()
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(run, job): job for job in jobs}
            for future in concurrent.futures.as_completed(futures):
                job = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 — one chapter must not end the run
                    print(f"  глава {job['chapter_index']:>3} ОШИБКА: {exc}")
                    all_problems.append(f"глава {job['chapter_index']}: {exc}")
                    continue
                stats = result["stats"]
                stored = store_attributions(db, result["records"])
                db.add(LlmUsageLog(
                    book_id=book.id,
                    chapter_id=result["chapter_id"],
                    chapter_index=result["chapter_index"],
                    stage="v2_attribution",
                    provider=args.provider,
                    model=args.model,
                    created_by_user_id=book.created_by_user_id or "",
                    created_by_name=book.created_by_name or "",
                    prompt_tokens=int(stats["prompt_tokens"]),
                    completion_tokens=int(stats["completion_tokens"]),
                    total_tokens=int(stats["prompt_tokens"]) + int(stats["completion_tokens"]),
                ))
                db.commit()
                stored_total += stored
                merge_stats(totals, stats)
                all_problems.extend(result["problems"])
                print(f"  глава {result['chapter_index']:>3} {result['title'][:28]:<28} "
                      f"сегментов {len(result['units']) + len(result['headings']):>4}  вызовов {stats['calls']:>3}  "
                      f"токены {stats['prompt_tokens']:>7}/{stats['completion_tokens']:<6} "
                      f"проблем {len(result['problems']):>3}  строк {stored:>4}  {result['elapsed']:>6.0f}с")

    print(f"\nитого: вызовов {totals['calls']}, токены {totals['prompt_tokens']} prompt / "
          f"{totals['completion_tokens']} completion, проблем {len(all_problems)}, "
          f"строк в v2_attributions {stored_total}, {time.monotonic() - started:.0f}с")
    if all_problems:
        print("\nпервые проблемы:")
        for problem in all_problems[:10]:
            print(f"   {problem[:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
