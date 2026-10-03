#!/usr/bin/env python
"""Проход по срокам проб и ролей. Запускается таймером раз в 5 минут.

    python scripts/run_deadlines.py

Закрывает сданное, шлёт письма о рекасте, напоминания диктору (за сутки и по
просрочке, по разу) и утреннюю сводку владельцу в 10:00 МСК. Пустой проход молчит.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.services.bot_contacts import report_pass as report_contacts  # noqa: E402
from app.services.role_deadlines import run_pass  # noqa: E402
from app.services.telegram import send_telegram_message  # noqa: E402


def main() -> int:
    with SessionLocal() as db:
        send = lambda chat, text: bool(send_telegram_message(db, text, chat_ids=[chat], direct=True))  # noqa: E731
        stats = run_pass(db, send=send)
        db.commit()
        # Сводка «кто вышел на связь с ботом» — тем же таймером, не чаще раза в 30 минут.
        stats["contacts"] = int(report_contacts(db, send=send))
    if any(stats.values()):
        print(" ".join(f"{key}={value}" for key, value in stats.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
