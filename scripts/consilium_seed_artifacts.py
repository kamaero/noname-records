#!/usr/bin/env python3
"""Перенести ответы чтецов прошлого прогона в артефакты движка — без обращения к моделям.

Ответы 2026-09-12 не знали отпечатка текста. Отпечаток ставится по НЫНЕШНЕМУ тексту главы:
текст с тех пор не менялся, а глава, где у чтеца нет ответа хотя бы на один абзац, пишется
неполной — пересверка её дочитает, а не примет пропуск за согласие.

Использование:
    python3 scripts/consilium_seed_artifacts.py <каталог ch<NN>-<reader>.json> --book <id> [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def seed(db, *, book_id: str, source_dir: str, root: str) -> dict:
    from app.services.consilium_engine import artifact_path, load_book_state, readers, save_answers

    state = load_book_state(db, book_id)
    slots = [(slot, model) for slot, _provider, model in readers()]
    models = dict(slots)
    out = {"seeded": 0, "incomplete": 0, "skipped_existing": 0, "unmatched": 0}
    chapter_indices = {ch.index for ch in state.chapters}

    for chapter in state.chapters:
        for reader, model in slots:
            src = os.path.join(source_dir, f"ch{chapter.index:02d}-{reader}.json")
            if not os.path.isfile(src):
                continue
            if os.path.exists(artifact_path(book_id, chapter.index, reader, root)):
                out["skipped_existing"] += 1
                continue
            with open(src, encoding="utf-8") as handle:
                data = json.load(handle)
            answers = {int(k): str(v) for k, v in (data.get("answers") or {}).items()}
            complete = {n for n, _ in chapter.paragraphs} <= set(answers)
            save_answers(book_id, chapter, reader, models[reader], answers, complete, root)
            out["seeded" if complete else "incomplete"] += 1

    # Count source files for chapters not in book state
    if os.path.isdir(source_dir):
        for filename in os.listdir(source_dir):
            match = re.match(r'ch(\d+)-(opus|sol)\.json$', filename)
            if match:
                chapter_index = int(match.group(1))
                if chapter_index not in chapter_indices:
                    out["unmatched"] += 1

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source")
    parser.add_argument("--book", required=True)
    parser.add_argument("--apply", action="store_true", help="записать (иначе только посчитать)")
    args = parser.parse_args()

    from app.db import SessionLocal
    from app.services.consilium_engine import ARTIFACT_ROOT

    if args.apply:
        root = ARTIFACT_ROOT
        with SessionLocal() as db:
            out = seed(db, book_id=args.book, source_dir=args.source, root=root)
        print(json.dumps(out, ensure_ascii=False))
    else:
        root = tempfile.mkdtemp(prefix="consilium-seed-dry-run-")
        try:
            with SessionLocal() as db:
                out = seed(db, book_id=args.book, source_dir=args.source, root=root)
            print(json.dumps(out, ensure_ascii=False), "(холостой прогон, записано во временный каталог)")
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
