#!/usr/bin/env python3
"""Пересчитать сверку по сохранённому услышанному — без обращения к распознаванию.

Зачем. Распознавание платное и невоспроизводимое: робот слушает файл один раз. Сверка
со сценарием — своя, бесплатная, и меняется у нас постоянно. С графой `heard_json`
(ревизия 0025) вход сверки остаётся в базе, и любую её правку можно применить ко всему
архиву этим скриптом, вместо того чтобы платить за распознавание второй раз.

Джобы, посчитанные до ревизии 0025, пропускаются: у них вход выброшен, и переигрывать
нечем. Это видно в отчёте отдельной строкой, а не молча.

Холостой прогон по умолчанию. Печатает, что изменилось бы: покрытие, число пропусков и
число реплик с несколькими подходами — до и после. Запись только по `--apply`.

Диктору ничего не отправляется ни в одном режиме: пересчёт — не событие записи
(см. `app/services/asr_replay.py`).

Использование:
    python3 scripts/realign_asr_jobs.py                  # холостой по всем джобам
    python3 scripts/realign_asr_jobs.py --chapter 29      # только глава 29
    python3 scripts/realign_asr_jobs.py --job <id>        # одна джоба
    python3 scripts/realign_asr_jobs.py --apply           # записать пересчёт
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="записать пересчёт в базу (иначе холостой прогон)")
    parser.add_argument("--job", default="", help="только эта джоба")
    parser.add_argument("--chapter", type=int, default=0, help="только главы с этим номером")
    args = parser.parse_args()

    from app.db import SessionLocal
    from app.models import AsrJob
    from app.services.asr_replay import realign_job

    with SessionLocal() as db:
        query = db.query(AsrJob).filter(AsrJob.status == "done")
        if args.job:
            query = query.filter(AsrJob.id == args.job)
        if args.chapter:
            query = query.filter(AsrJob.expected_chapter_index == args.chapter)
        jobs = query.order_by(AsrJob.expected_chapter_index.asc()).all()

        print(f"{'гл':>3} {'роль':18} {'покрытие':>17} {'пропуски':>10} {'многодубль':>12}")
        changed = skipped = 0
        for job in jobs:
            report = realign_job(db, str(job.id), write=args.apply)
            if report is None:
                skipped += 1
                continue
            before, after = report["before"], report["after"]
            mark = "→" if report["changed"] else " "
            if report["changed"]:
                changed += 1
            print(f"{report['chapter_index']:>3} {report['role'][:18]:18} "
                  f"{before['coverage']:>7.3f} {mark} {after['coverage']:<7.3f} "
                  f"{before['missing']:>4} {mark} {after['missing']:<4} "
                  f"{before['multi_take']:>5} {mark} {after['multi_take']:<5}")
        if args.apply:
            db.commit()

    print(f"\nвсего джоб {len(jobs)} | пересчитано иначе {changed} | "
          f"переигрывать нечем {skipped} (посчитаны до ревизии 0025)")
    print("записано в базу" if args.apply else "холостой прогон: в базу не писалось (--apply чтобы записать)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
