#!/usr/bin/env python3
"""Что правка сверки ASR сделала бы с архивом: сколько реплик нашлось, сколько потерялось.

Только чтение: база открывается в режиме `mode=ro`, ничего не пишется и не коммитится.
Пересчитывает `align_transcript` текущего кода для каждой `AsrJob` со статусом `done` и
непустым `heard_json` и сравнивает с «прежней» сверкой.

Прежняя сверка — по умолчанию сохранённый `alignment_json`. Но его часть дописана
`borrow_across_roles` (реплика найдена в файле соседней роли), и заимствованное в чистом
пересчёте выглядело бы «потерей», хотя сверка тут ни при чём. Поэтому для замера правки
есть `--old-ref REF`: прежняя сверка считается кодом `app/services/asr_align.py` из
этого коммита git на том же услышанном и том же сценарии — разница тогда ровно в правке.
`REF` — коммит ДО правки (для правки коротких реплик 28.10 — `a51f809`), а не `main`:
после слияния `main` уже несёт правку, и сравнение покажет ноль.

    PYTHONPATH=. python scripts/asr_alignment_diff.py --db /tmp/copy.db --old-ref a51f809

Печатает итог «+N найдено, −M потеряно, ~K сдвинуто», джобы с потерями и сдвигами и
список глав, где сверка изменилась, — по нему пересчитывают прод (`realign_chapter`).
«Сдвинуто» — реплика найдена и до, и после, но первый подход сменил место больше чем на
`MOVE_TOLERANCE` секунд: без этого счёта реплика, переехавшая на чужое место, была бы не
видна вовсе, а «найденная» на чужом месте выглядела бы чистым выигрышем.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


#: на сколько секунд может сдвинуться первый подход, чтобы это не считалось переездом
MOVE_TOLERANCE = 0.5


def _old_align(ref: str):
    """`align_transcript` из коммита `ref` — отдельным модулем, не трогая текущий."""
    source = subprocess.run(
        ["git", "-C", _ROOT, "show", f"{ref}:app/services/asr_align.py"],
        check=True, capture_output=True, text=True,
    ).stdout
    module = types.ModuleType(f"asr_align_{ref}")
    exec(compile(source, f"{ref}:asr_align.py", "exec"), module.__dict__)
    return module.align_transcript


def _moved(old: dict, new: dict) -> list[str]:
    """Реплики, найденные в обеих сверках, но в разных местах записи."""
    moved = []
    for before, after in zip(old.get("lines") or [], new.get("lines") or []):
        if not (before.get("matched") and after.get("matched")):
            continue
        shift = max(abs(float(before.get("start") or 0) - float(after.get("start") or 0)),
                    abs(float(before.get("end") or 0) - float(after.get("end") or 0)))
        if shift > MOVE_TOLERANCE:
            moved.append(f"{str(after.get('text') or '')[:40]} "
                         f"({before.get('start')}→{after.get('start')})")
    return moved


def _matched(alignment: dict) -> dict[int, str]:
    return {int(line["index"]): str(line.get("text") or "")
            for line in alignment.get("lines") or [] if line.get("matched")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="путь к копии базы (открывается только на чтение)")
    parser.add_argument("--old-ref", default="", help="коммит git с прежней сверкой; без него — сохранённая")
    args = parser.parse_args(argv)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import AsrJob, ScriptBook, ScriptChapter
    from app.services.asr_align import align_transcript
    from app.services.asr_run import expected_lines

    engine = create_engine(f"sqlite:///file:{os.path.abspath(args.db)}?mode=ro&uri=true")
    old_align = _old_align(args.old_ref) if args.old_ref else None

    found = lost = shifted = jobs_seen = 0
    with_losses: list[tuple] = []
    with_moves: list[tuple] = []
    changed_chapters: dict[str, dict] = {}
    with sessionmaker(bind=engine)() as db:
        jobs = (db.query(AsrJob).filter(AsrJob.status == "done", AsrJob.heard_json != "")
                .order_by(AsrJob.expected_chapter_index.asc()).all())
        for job in jobs:
            heard = json.loads(job.heard_json)
            segments = heard.get("segments") or []
            expected = expected_lines(db, str(job.chapter_id or ""), str(job.expected_role or ""))
            new = align_transcript(expected, segments)
            if old_align is not None:
                old = old_align(expected, segments)
            else:
                old = json.loads(job.alignment_json) if str(job.alignment_json or "").strip() else {}
            jobs_seen += 1
            before, after = _matched(old), _matched(new)
            gained = sorted(set(after) - set(before))
            dropped = sorted(set(before) - set(after))
            found += len(gained)
            lost += len(dropped)
            if dropped:
                with_losses.append((job, [before[i] for i in dropped]))
            moved = _moved(old, new)
            shifted += len(moved)
            if moved:
                with_moves.append((job, moved))
            same_layout = (json.dumps(new.get("lines"), sort_keys=True)
                           == json.dumps(old.get("lines"), sort_keys=True))
            if gained or dropped or not same_layout:
                info = changed_chapters.setdefault(str(job.chapter_id),
                                                   {"found": 0, "lost": 0, "moved": 0, "jobs": 0})
                info["found"] += len(gained)
                info["lost"] += len(dropped)
                info["moved"] += len(moved)
                info["jobs"] += 1
                if gained:
                    print(f"+ гл {job.expected_chapter_index} {job.expected_role}: "
                          + " | ".join(after[i][:50] for i in gained))

        print(f"\nсверок: {jobs_seen}; +{found} найдено, −{lost} потеряно, ~{shifted} сдвинуто")
        print(f"\nджоб с потерями: {len(with_losses)}")
        for job, texts in with_losses:
            print(f"  {job.id} гл {job.expected_chapter_index} {job.expected_role}: "
                  + " | ".join(text[:50] for text in texts))

        print(f"\nджоб со сдвигами: {len(with_moves)}")
        for job, moves in with_moves:
            print(f"  {job.id} гл {job.expected_chapter_index} {job.expected_role}: " + " | ".join(moves))

        print(f"\nглав с изменённой сверкой: {len(changed_chapters)}")
        for chapter_id, info in changed_chapters.items():
            chapter = db.get(ScriptChapter, chapter_id)
            book = db.get(ScriptBook, chapter.book_id) if chapter is not None else None
            print(f"  {chapter_id}  {getattr(book, 'title', '?')} / гл {getattr(chapter, 'chapter_index', '?')}"
                  f"  +{info['found']} −{info['lost']} ~{info['moved']} (джоб {info['jobs']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
