#!/usr/bin/env python3
"""Включить отдельное чтение названий одной книги; по умолчанию только отчёт."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-id", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from app.db import SessionLocal
    from app.services.chapter_titles import configure

    with SessionLocal() as db:
        report = configure(db, args.book_id, args.actor, apply=args.apply)
        if args.apply:
            db.commit()
        else:
            db.rollback()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
