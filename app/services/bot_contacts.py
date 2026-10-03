"""Кто вышел на связь с ботом (01.10): учёт контактов и сводка владельцу раз в 30 минут.

Бот записывает каждого, кто ему пишет или жмёт кнопку (`touch`), — без текстов. Сводка
(`report_pass`, из прохода по таймеру) называет новых: с учёткой — по имени в системе, без
учётки — как назван в Telegram, с @username и id, и из базы голосов ли он; плюс кто брал
пароль (повторы подсвечены — признак проблем со входом). Нового нет — молчит.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from app.models import AuditLog, BotContact, TelegramAuthAccount, User
from app.time_utils import utcnow_naive

REPORT_EVERY = timedelta(minutes=30)
TELEGRAM_LOGIN_PREFIX = "tg_"


def touch(db, sender: dict, *, now: datetime) -> None:
    tid = str((sender or {}).get("id") or "").strip()
    if not tid:
        return
    name = " ".join(part for part in (str(sender.get("first_name") or "").strip(),
                                      str(sender.get("last_name") or "").strip()) if part)[:200]
    row = db.get(BotContact, tid)
    if row is None:
        row = BotContact(telegram_user_id=tid, first_seen_at=now)
        db.add(row)
    row.last_seen_at = now
    row.tg_name = name or row.tg_name or ""
    row.username = str(sender.get("username") or row.username or "")[:64]
    db.flush()


def _account_name(db, tid: str) -> str:
    """Имя учётки этого Telegram — так же, как бот узнаёт человека (`bot_dialog._person`)."""
    row = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.telegram_user_id == tid,
                                               TelegramAuthAccount.is_active == "true").first()
    user = db.get(User, row.user_id) if row is not None and row.user_id else None
    if user is None:
        user = db.query(User).filter(User.login == f"{TELEGRAM_LOGIN_PREFIX}{tid}").first()
    return str(user.display_name or "") if user is not None and str(user.is_active or "") == "true" else ""


def _unknown_line(row: BotContact) -> str:
    who = row.tg_name or "без имени"
    at = f" @{row.username}" if row.username else ""
    return f"{who}{at} (id {row.telegram_user_id})"


def summary(db, *, contacts: list[BotContact], since: datetime) -> str:
    known, unknown = [], []
    for row in contacts:
        name = _account_name(db, row.telegram_user_id)
        (known if name else unknown).append(name or _unknown_line(row))
    passwords = Counter(
        getattr(db.get(User, a.user_id), "display_name", a.user_id)
        for a in db.query(AuditLog).filter(AuditLog.action == "password_issued_by_bot", AuditLog.created_at > since).all()
    )
    parts = []
    if known:
        parts.append("Вышли на связь:\n" + "\n".join(f"• {name}" for name in sorted(known)))
    if unknown:
        parts.append("Без учётки — назвать?\n" + "\n".join(f"• {line}" for line in unknown))
    if passwords:
        parts.append("Брали пароль:\n" + "\n".join(f"• {name}" + (f" ×{n}" if n > 1 else "")
                                                for name, n in sorted(passwords.items())))
    return "\n\n".join(parts)


def report_pass(db, *, now: datetime | None = None, send) -> bool:
    """Сводка владельцу не чаще раза в 30 минут; нечего сказать — молчит. True — отправлена."""
    from app.config import settings
    from app.services.studio_settings import studio_settings

    now = now or utcnow_naive()
    owner = str(settings.owner_telegram_id or "").strip()
    row_settings = studio_settings(db)
    last = row_settings.contacts_reported_at
    if last is not None and now - last < REPORT_EVERY:
        return False
    since = last or (now - timedelta(days=1))
    contacts = db.query(BotContact).filter(BotContact.reported_at.is_(None)).order_by(BotContact.first_seen_at).all()
    text = summary(db, contacts=contacts, since=since)
    for row in contacts:
        row.reported_at = now
    row_settings.contacts_reported_at = now
    db.commit()  # отметки — до отправки: упавшая отправка не повторит сводку
    if text and owner:
        send(owner, f"Бот: кто вышел на связь\n\n{text}")
        return True
    return False


def who_now(db, *, now: datetime | None = None) -> str:
    """Ответ на «/кто»: за сутки — новые контакты и пароли; все без учётки — всегда."""
    now = now or utcnow_naive()
    day = now - timedelta(days=1)
    recent = db.query(BotContact).filter(BotContact.first_seen_at >= day).order_by(BotContact.first_seen_at).all()
    without = [row for row in db.query(BotContact).all() if not _account_name(db, row.telegram_user_id)]
    seen = {row.telegram_user_id for row in recent}
    text = summary(db, contacts=recent + [row for row in without if row.telegram_user_id not in seen], since=day)
    total = db.query(BotContact).count()
    return f"Контактов с ботом всего: {total}. За сутки:\n\n{text}" if text else f"Контактов с ботом всего: {total}. За сутки нового нет."
