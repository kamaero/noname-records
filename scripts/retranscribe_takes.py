#!/usr/bin/env python3
"""Перераспознать присланные дубли заново — и записать, что услышал робот.

Зачем это вообще нужно. Джобы, посчитанные до ревизии 0025, хранят только ИТОГ сверки;
её вход — сегменты со словными таймингами — выброшен. Ни дубли реплики на таймлайне, ни
любая будущая правка сверки к такому архиву не применяются: переигрывать нечем, кроме
повторной оплаты распознавания. Этот скрипт её и тратит — один раз, чтобы больше не
пришлось: после него у каждой джобы есть `heard_json`, и дальше сверка пересчитывается
бесплатно (`scripts/realign_asr_jobs.py`).

Диктору не отправляется ничего: прогон идёт по нашей инициативе, а не потому, что он
что-то прислал. Письмо «у вас пропущены реплики» по дублю, сданному месяц назад,
сказало бы ему неправду о его работе и отправило перезаписывать уже принятое
(`notify=False` в `app/services/asr_run.py`).

Холостой прогон по умолчанию: показывает состав, минуты аудио и баланс — не трогая ни
базу, ни сеть. Работа только по `--apply`, и только с собственноручно снятой копией
базы: прогон перезаписывает расшифровки и сверку у каждой джобы, а распознавание
недетерминировано — вернуть прежнее можно будет только из копии.

Деньги. Баланс RouterAI читается до и после прогона, и разница печатается как
стоимость — вместе со стоимостью минуты аудио. Это замер по факту, а не оценка по
прайсу.

Использование:
    python3 scripts/retranscribe_takes.py                       # холостой: состав и баланс
    python3 scripts/retranscribe_takes.py --chapter 29          # только глава 29
    python3 scripts/retranscribe_takes.py --apply --backup /путь/копия.db
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.routerai_credits import read_credits as _read_credits  # noqa: E402


def read_credits() -> float | None:
    """Баланс RouterAI. `None` — не ответил: это не повод останавливать прогон."""
    return _read_credits(log=print)


def backup_database(target: str) -> None:
    """Копия базы через `.backup` — обычный `cp` не видит WAL и даёт рваную копию."""
    from app.config import settings

    source = str(settings.database_url or "").replace("sqlite:///", "", 1)
    source = os.path.abspath(source)
    subprocess.run(["sqlite3", source, f".backup {target}"], check=True, timeout=600)
    if not os.path.isfile(target) or os.path.getsize(target) == 0:
        raise SystemExit(f"копия базы не снялась: {target}")
    print(f"✓ копия базы: {target} ({os.path.getsize(target) / 1048576:.0f} МБ)")


def _shape(job) -> dict:
    """Покрытие, пропуски и многодубльность джобы — то, по чему видно, что прогон сделал."""
    try:
        payload = json.loads(job.alignment_json or "{}")
    except ValueError:
        payload = {}
    lines = payload.get("lines") or []
    return {
        "coverage": float(payload.get("coverage") or 0.0),
        "missing": len(payload.get("missing") or []),
        "multi_take": sum(1 for line in lines if len(line.get("takes") or []) > 1),
        "heard": bool(str(getattr(job, "heard_json", "") or "").strip()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="распознавать по-настоящему (тратит деньги)")
    parser.add_argument("--backup", default="", help="куда снять копию базы перед работой (обязательно с --apply)")
    parser.add_argument("--chapter", type=int, default=0, help="только главы с этим номером")
    parser.add_argument("--report", default="", help="куда положить JSON-отчёт о прогоне")
    args = parser.parse_args()

    if args.apply and not args.backup:
        raise SystemExit("--apply без --backup не работает: прогон перезаписывает расшифровки, "
                         "а распознавание недетерминировано — вернуть прежнее можно только из копии")

    from app.db import SessionLocal
    from app.models import AsrJob, AudioFile
    from app.services.asr_run import AsrRunError, find_chapter_for_take, run_asr_for_take
    from app.services.audio_uploads import TAKE

    with SessionLocal() as db:
        takes = (
            db.query(AudioFile)
            .filter(AudioFile.kind == TAKE)
            .order_by(AudioFile.uploaded_at.asc())
            .all()
        )
        if args.chapter:
            takes = [t for t in takes
                     if (c := find_chapter_for_take(db, t)) is not None
                     and int(c.chapter_index or 0) == args.chapter]
        minutes = sum(float(t.duration_seconds or 0.0) for t in takes) / 60.0
        print(f"дублей {len(takes)} | аудио {minutes:.1f} мин")

        credits_before = read_credits()
        if credits_before is not None:
            print(f"баланс до: {credits_before:.2f} ₽")

        if not args.apply:
            for take in takes:
                print(f"  {str(take.chapter)[:34]:34} {str(take.role)[:16]:16} "
                      f"{float(take.duration_seconds or 0.0) / 60:5.1f} мин")
            print("\nхолостой прогон: распознавание не запускалось, база не тронута, "
                  "денег не потрачено (--apply --backup <путь> чтобы работать)")
            return 0

        backup_database(args.backup)
        rows, failed = [], 0
        started = time.monotonic()
        for number, take in enumerate(takes, start=1):
            job_before = db.query(AsrJob).filter(AsrJob.audio_file_id == take.id).one_or_none()
            before = _shape(job_before) if job_before is not None else {}
            clock = time.monotonic()
            try:
                job = run_asr_for_take(db, str(take.id), notify=False)
            except AsrRunError as exc:
                failed += 1
                print(f"{number:>3}/{len(takes)} ✗ {str(take.role)[:16]:16} {exc}")
                continue
            # Коммит после каждого файла: распознавание уже оплачено, и прерванный на
            # середине прогон не должен стоить ещё раз.
            db.commit()
            after = _shape(job)
            rows.append({
                "audio_file_id": str(take.id),
                "chapter": str(take.chapter or ""),
                "role": str(take.role or ""),
                "minutes": round(float(take.duration_seconds or 0.0) / 60.0, 2),
                "seconds_spent": round(time.monotonic() - clock, 1),
                "status": str(job.status or ""),
                "before": before,
                "after": after,
            })
            arrow = "=" if before.get("coverage") == after["coverage"] else "→"
            print(f"{number:>3}/{len(takes)} ✓ {str(take.role)[:16]:16} "
                  f"покрытие {before.get('coverage', 0.0):.3f} {arrow} {after['coverage']:.3f} | "
                  f"пропуски {before.get('missing', 0)} {arrow} {after['missing']} | "
                  f"многодубль {before.get('multi_take', 0)} {arrow} {after['multi_take']} | "
                  f"{time.monotonic() - clock:.0f} с")

    spent = time.monotonic() - started
    credits_after = read_credits()
    print(f"\nраспознано {len(rows)} | не вышло {failed} | потрачено времени {spent / 60:.1f} мин")
    cost = None
    if credits_before is not None and credits_after is not None:
        cost = credits_before - credits_after
        print(f"баланс: {credits_before:.2f} → {credits_after:.2f} ₽ | прогон стоил {cost:.2f} ₽")
        if minutes > 0:
            print(f"цена минуты аудио: {cost / minutes:.2f} ₽ | цена часа: {cost / minutes * 60:.2f} ₽")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as handle:
            json.dump({"takes": rows, "failed": failed, "minutes": round(minutes, 1),
                       "credits_before": credits_before, "credits_after": credits_after,
                       "cost": cost, "seconds": round(spent, 1)}, handle, ensure_ascii=False, indent=2)
        print(f"отчёт: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
