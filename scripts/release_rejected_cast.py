"""Освободить старые предварительные назначения с двумя оценками ниже 7.

Без --apply изменения откатываются; уведомления и оценки сохраняются.
"""
from __future__ import annotations

import argparse

from app.db import SessionLocal
from app.models import AuditionRejection
from app.services.audition_rejections import (
    CAUSE_SCORES, FINAL, PENDING, release_tentative_after_rejection, scores_reject,
)


def repair(db, *, apply=False):
    ids = [row.id for row in db.query(AuditionRejection).filter(
        AuditionRejection.cause == CAUSE_SCORES,
        AuditionRejection.status.in_({PENDING, *FINAL}),
    ).order_by(AuditionRejection.id)]
    changed = []
    for rid in ids:
        row = db.get(AuditionRejection, rid)
        if scores_reject(db, book_id=row.book_id, role=row.role,
                         actor_name=row.actor_name) and release_tentative_after_rejection(db, row):
            changed.append({"book_id": row.book_id, "role": row.role, "actor_name": row.actor_name})
            if apply:
                db.commit()
                continue
        db.rollback()
    return changed


def main():
    from app.config import settings

    # Пересборка назначений сверяет сроки всей книги; холостой прогон не шлёт писем.
    settings.telegram_notify_enabled = False
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        for item in repair(db, apply=args.apply):
            print(item)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

