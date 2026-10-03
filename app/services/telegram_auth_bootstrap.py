from __future__ import annotations

import logging
from dataclasses import dataclass

from app.config import settings
from app.db import SessionLocal
from app.models import TelegramAuthAccount

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramWhitelistEntry:
    telegram_user_id: str
    display_name: str


def parse_telegram_auth_whitelist(raw_value: str) -> list[TelegramWhitelistEntry]:
    entries: list[TelegramWhitelistEntry] = []
    seen: set[str] = set()
    for raw_item in (raw_value or "").split(";"):
        item = raw_item.strip()
        if not item:
            continue
        raw_id, sep, raw_name = item.partition("|")
        telegram_user_id = raw_id.strip()
        display_name = raw_name.strip() if sep else ""
        if not telegram_user_id or not telegram_user_id.isdigit() or not display_name:
            logger.warning("Skipping invalid TELEGRAM_AUTH_WHITELIST entry: %s", item)
            continue
        if telegram_user_id in seen:
            continue
        seen.add(telegram_user_id)
        entries.append(TelegramWhitelistEntry(telegram_user_id=telegram_user_id, display_name=display_name[:120]))
    return entries


def bootstrap_telegram_auth_whitelist() -> int:
    entries = parse_telegram_auth_whitelist(settings.telegram_auth_whitelist)
    if not entries:
        return 0

    default_role = (settings.telegram_auth_whitelist_default_role or "author").strip() or "author"
    default_access_scope = (settings.telegram_auth_whitelist_default_access_scope or "full").strip() or "full"

    changed = 0
    with SessionLocal() as db:
        for entry in entries:
            row = db.query(TelegramAuthAccount).filter(
                TelegramAuthAccount.telegram_user_id == entry.telegram_user_id
            ).first()
            if not row:
                db.add(
                    TelegramAuthAccount(
                        telegram_user_id=entry.telegram_user_id,
                        display_name=entry.display_name,
                        role=default_role,
                        access_scope=default_access_scope,
                        is_active="true",
                    )
                )
                changed += 1
                continue
            before = (row.display_name, row.is_active)
            row.display_name = entry.display_name
            row.is_active = "true"
            if not (row.role or "").strip():
                row.role = default_role
            if not (row.access_scope or "").strip():
                row.access_scope = default_access_scope
            if before != (row.display_name, row.is_active):
                changed += 1
        db.commit()

    logger.info("Telegram auth whitelist bootstrap applied: entries=%s changed=%s", len(entries), changed)
    return changed
