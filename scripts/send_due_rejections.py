#!/usr/bin/env python
"""Разослать созревшие отказы по пробам. Запускается таймером раз в минуту.

    python scripts/send_due_rejections.py

Срок каждого отказа лежит в базе (`audition_rejections.due_at`); скрипт только берёт
созревшие. Пустой проход — норма и молчит.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.services.audition_reactions import send_due_rejections  # noqa: E402


def main() -> int:
    with SessionLocal() as db:
        for rejection_id, status in send_due_rejections(db):
            print(f"{rejection_id}: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
