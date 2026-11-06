#!/usr/bin/env python3
"""Выгрузить из базы то, что нужно для проверки сверки ASR без базы: что ожидалось
и что услышал робот (`AsrJob.heard_json`). Только чтение.

Зачем: промахи сверки на коротких репликах видны лишь на живых записях, а
распознавание платно и невоспроизводимо — поэтому услышанное снимаем в фикстуру
и дальше гоняем `align_transcript` бесплатно.

    # одна глава: {"expected": [...], "segments": [...]}
    python3 -m scripts.export_asr_fixture --audio EX_Ch55_Role_Actor.wav \
        --chapter 55 --role Роль --out /tmp/asr-chapter.json
    # запись целой роли: {"chapters": {"1": [...], ...}, "segments": [...]}
    python3 -m scripts.export_asr_fixture --audio EX_Ch01_Role_Actor.wav \
        --whole-roles --role Роль --out /tmp/asr-role.json

Выгрузка может содержать чужие тексты и имена: не добавляйте её в публичный репозиторий.
"""
from __future__ import annotations

import argparse
import json


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True, help="canonical_filename записи")
    parser.add_argument("--role", required=True)
    parser.add_argument("--chapter", type=int, default=0)
    parser.add_argument("--whole-roles", action="store_true",
                        help="ожидаемое — по всем главам роли, а не по одной")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if not args.whole_roles and not args.chapter:
        parser.error("нужен --chapter или --whole-roles")

    from app import db as app_db
    from app.models import AsrJob, AudioFile, Character, ScriptChapter
    from app.services.asr_run import expected_lines
    from app.services.role_deadlines import role_chapters

    db = app_db.SessionLocal()
    try:
        audio = db.query(AudioFile).filter(AudioFile.canonical_filename == args.audio).first()
        if audio is None:
            raise SystemExit(f"нет записи {args.audio}")
        jobs = (db.query(AsrJob).filter(AsrJob.audio_file_id == audio.id, AsrJob.heard_json != "")
                .order_by(AsrJob.created_at.desc()).all())
        if not jobs:
            raise SystemExit("у записи нет сохранённого распознавания")
        job = jobs[0]
        segments = json.loads(job.heard_json).get("segments") or []
        chapter = db.get(ScriptChapter, job.chapter_id)
        if chapter is None:
            raise SystemExit("у джобы нет главы")

        if not args.whole_roles:
            ch = (db.query(ScriptChapter).filter(ScriptChapter.book_id == chapter.book_id,
                  ScriptChapter.chapter_index == args.chapter).first())
            if ch is None:
                raise SystemExit("нет такой главы")
            payload = {"expected": expected_lines(db, ch.id, args.role), "segments": segments}
        else:
            character = (db.query(Character).filter(Character.book_id == chapter.book_id,
                         Character.name == args.role).first())
            if character is None:
                raise SystemExit("нет такой роли")
            chapters = {}
            for index in sorted(role_chapters(db, character)):
                ch = (db.query(ScriptChapter).filter(ScriptChapter.book_id == chapter.book_id,
                      ScriptChapter.chapter_index == index).first())
                chapters[str(index)] = expected_lines(db, ch.id, args.role)
            payload = {"chapters": chapters, "segments": segments}
    finally:
        db.close()
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    print(f"записано {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
