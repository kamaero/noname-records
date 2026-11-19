#!/usr/bin/env python3
"""Импорт двух готовых WAV одной сцены; без --apply только проверка."""
import argparse
import fcntl
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapter-id", required=True)
    parser.add_argument("--marker-id")
    parser.add_argument("--list-scenes", action="store_true")
    parser.add_argument("--a")
    parser.add_argument("--b")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from app.db import SessionLocal
    from app.services import audio_storage
    from app.services.ambient_pairs import import_pair
    if args.list_scenes:
        from app.services.chapter_delivery import active_scene_rows
        from app.models import ScriptChapter
        with SessionLocal() as db:
            if db.get(ScriptChapter, args.chapter_id) is None:
                parser.error("Chapter does not exist")
            rows, _ = active_scene_rows(db, args.chapter_id)
            print(json.dumps([{"marker_id": row["id"], "segment_id": row["segment_id"]} for row in rows], indent=2))
        return
    if not args.marker_id or not args.a or not args.b:
        parser.error("Import requires --marker-id, --a and --b")
    # Все CLI-импорты установки сериализованы; два запуска не заменяют друг друга.
    root = audio_storage.local_root()
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, ".ambient-pair-import.lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with SessionLocal() as db:
            report = import_pair(db, chapter_id=args.chapter_id, marker_id=args.marker_id,
                variant_a=args.a, variant_b=args.b, apply=args.apply)
            if args.apply:
                db.commit()
            else:
                db.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
