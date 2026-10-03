#!/usr/bin/env python3
"""Пересчитать сверку глав с поиском у соседних ролей. По умолчанию — холостой прогон.

Зачем. `realign_chapter` (см. `app/services/asr_replay.py`) применяет к уже принятым
дублям текущие правила сверки: чистый пересчёт по услышанному и заимствование реплики
у соседней роли того же актёра. Этот скрипт прогоняет её по главам архива — по одной
или по всем сразу — и показывает, что изменилось бы, прежде чем это тронет боевую базу.

`lost_matches` — контрольная проверка самого пересчёта, а не находка про роли: строка,
найденная до и ненайденная после при том же тексте, означает, что новое правило сверки
хуже старого, а не лучше. Такого быть не должно; если это случилось — прогон нужно
остановить и разобраться, а не применять.

    uv run python scripts/realign_chapters.py                       # все главы с джобами, без записи
    uv run python scripts/realign_chapters.py --chapter <id> --apply --notify
    uv run python scripts/realign_chapters.py --apply               # всё, без писем
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def lost_matches(before: dict, after_jobs) -> list[tuple[str, str]]:
    """Тексты, найденные в джобе до пересчёта и потерянные после, — по тексту, а не по номеру.

    После переноса реплики все строки роли за ней сдвигаются на номер, и сравнение по
    позиции не проверяло бы ничего. Сравниваются мультимножества найденных текстов;
    текст, которого джоба больше не ждёт (реплику унесли), потерей не считается.
    """
    from collections import Counter

    lost = []
    for job, _audio, after in after_jobs:
        after_lines = after.get("lines") or []
        expected = Counter(line.get("text") for line in after_lines)
        was = Counter(line.get("text") for line in before.get(str(job.id), {}).get("lines") or []
                      if line.get("matched"))
        # Найденных «до» больше, чем джоба теперь ждёт, — лишние унесли, это не потеря.
        was = was & expected
        now = Counter(line.get("text") for line in after_lines if line.get("matched"))
        for text, count in (was - now).items():
            lost.extend([(str(job.expected_role), text)] * count)
    return lost


def run(db, chapter_ids, *, apply: bool, notify: bool) -> list[dict]:
    """Пересчитать перечисленные главы; вернуть отчёт по каждой — печатает и коммитит по пути.

    По главе, а не по всему разом: каждая глава коммитится (или откатывается) сразу после
    своего пересчёта — долгий прогон по всему архиву не должен потерять уже посчитанное
    из-за ошибки на одной из следующих глав. Письма — после коммита главы: письмо до
    упавшего коммита ушло бы снова на следующем прогоне.
    """
    from app.services.asr_borrow import chapter_done_jobs
    from app.services.asr_replay import realign_chapter, send_moved_letters

    reports = []
    for chapter_id in chapter_ids:
        # Снимок «до» — парсинг JSON-строки уже даёт независимую копию, ничего в сессии
        # ещё не тронуто: `realign_chapter` ниже это как раз изменит.
        before = {str(job.id): json.loads(job.alignment_json or "{}")
                  for job, _audio, _alignment in chapter_done_jobs(db, chapter_id)}
        report = realign_chapter(db, chapter_id, notify=False)
        lost = lost_matches(before, chapter_done_jobs(db, chapter_id))
        pending = report.pop("pending_letters")

        if apply:
            db.commit()
        else:
            db.rollback()
        notified = send_moved_letters(db, pending) if (apply and notify) else []
        result = {**report, "notified": len(notified), "lost_matches": lost}
        print(json.dumps(result, ensure_ascii=False))
        reports.append(result)
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chapter", action="append", default=[], help="только эта глава (можно несколько раз)")
    parser.add_argument("--apply", action="store_true", help="записать пересчёт в базу (иначе холостой прогон)")
    parser.add_argument("--notify", action="store_true", help="письма о новых пропусках (только вместе с --apply)")
    args = parser.parse_args()
    if args.apply and args.notify and not args.chapter:
        # Письма по всему архиву разом — не тот случай, который делают одной опечаткой.
        print("--apply --notify только вместе с --chapter: письма актёрам шлются по выбранным главам",
              file=sys.stderr)
        return 2

    from app.db import SessionLocal
    from app.models import AsrJob

    with SessionLocal() as db:
        chapter_ids = args.chapter or sorted({str(cid) for (cid,) in db.query(AsrJob.chapter_id).distinct() if cid})
        reports = run(db, chapter_ids, apply=args.apply, notify=args.notify)

    totals = {
        "chapters": len(reports),
        "changed": sum(1 for report in reports if report["changed"]),
        "borrowed": sum(report["borrowed"] for report in reports),
        "new_missing_lines": sum(len(texts) for report in reports for texts in report["new_missing"].values()),
        "lost_matches": sum(len(report["lost_matches"]) for report in reports),
    }
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
