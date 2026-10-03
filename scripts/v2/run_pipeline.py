"""Run the v2 book pipeline inline, against a named sqlite file, with a line per chapter.

The same `run_book_pipeline` the worker runs, only here in the foreground: the
progress lines the worker writes to the run row are printed instead. Useful for a
scratch copy, a dry rehearsal on one book before the worker gets it, or a machine
without Redis. Refuses the production database like the other v2 scripts.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

PROD_DB = "noname.db"


def main() -> int:
    from app.config import settings
    from app.v2.pipeline import STEPS

    parser = argparse.ArgumentParser()
    parser.add_argument("book_id")
    parser.add_argument("--db", required=True, help="путь к sqlite-файлу")
    parser.add_argument("--steps", nargs="+", choices=STEPS, default=list(STEPS), help="какие шаги (в порядке пайплайна)")
    parser.add_argument("--force", action="store_true", help="переделать и то, что уже сделано")
    parser.add_argument("--provider", default=settings.default_final_provider)
    parser.add_argument("--model", default=settings.default_final_model)
    parser.add_argument("--thinking", choices=("on", "off"), default="off", help="режим размышлений модели")
    parser.add_argument("--no-context", action="store_true", help="ударения без RUAccent")
    parser.add_argument("--i-mean-production", action="store_true")
    args = parser.parse_args()

    if os.path.realpath(args.db) == os.path.realpath(PROD_DB) and not args.i_mean_production:
        print("ОТКАЗ: это продовая база. v2 строится рядом с работающей системой, а не в ней.")
        return 2

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import ScriptBook
    from app.v2.pipeline import RunContext, run_book_pipeline
    from app.v2.store import ensure_v2_tables

    engine = create_engine(f"sqlite:///{args.db}")
    created = ensure_v2_tables(engine)
    if created:
        print(f"создаю недостающие таблицы v2: {', '.join(created)}")
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with SessionLocal() as db:
        book = db.get(ScriptBook, args.book_id)
        if book is None:
            print(f"книги {args.book_id} нет в {args.db}")
            return 1
        if str(book.stop_requested or "").lower() == "true":
            print("флаг остановки был поднят с прошлого раза — снимаю")
            book.stop_requested = "false"
            db.commit()
        print(f"книга: {book.title}   статус: {book.status}   база: {args.db}")

    ctx = RunContext(thinking=args.thinking, context=None if args.no_context else "auto")
    started = time.monotonic()
    run = run_book_pipeline(
        args.book_id,
        steps=tuple(args.steps),
        force=args.force,
        provider=args.provider,
        model=args.model,
        thinking=args.thinking,
        session_factory=SessionLocal,
        ctx=ctx,
        on_progress=lambda message: print(f"  {message}", flush=True),
    )
    print(f"\nпрогон {run.id}: {run.status}  шаг {run.step}  глав {run.chapters_done}/{run.chapters_total}  "
          f"вызовов {run.calls}  токены {run.prompt_tokens}/{run.completion_tokens}  {time.monotonic() - started:.0f}с")
    if run.error:
        print(f"ошибка: {run.error}")
    return 0 if run.status == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
