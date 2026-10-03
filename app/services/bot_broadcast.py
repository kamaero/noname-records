"""Рассылка из бота (решение владельца 01.10): по книге или всем дикторам сразу.

Ход: «📢 Рассылка» → книга или «Всем дикторам» → (для книги) утверждённым / + позванным на
пробу → текст → предпросмотр → «Отправить». Строка `bot_broadcasts` держит шаг разговора;
брошенная рассылка забывается через час. Получатели — по учёткам (не по именам каста):
`dictor_assignments` книги или все с ролью `dictor`; письмо — по их привязке Telegram.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from app.models import (
    AuditLog, BotBroadcast, DictorAssignment, ScriptBook, TelegramAuthAccount, User, UserRole,
)
from app.time_utils import utcnow_naive
from app.services import studio

TTL = timedelta(hours=1)
TELEGRAM_LIMIT = 4096
UNFINISHED = ("choose", "audience", "text", "confirm")


def people(n: int) -> str:
    """«1 человек», «2 человека», «5 человек», «22 человека»."""
    mod10, mod100 = n % 10, n % 100
    few = 2 <= mod10 <= 4 and not 12 <= mod100 <= 14
    return f"{n} {'человека' if few else 'человек'}"


def start(db, admin_tid: str, *, now: datetime) -> BotBroadcast:
    for old in db.query(BotBroadcast).filter(BotBroadcast.admin_tid == admin_tid,
                                             BotBroadcast.state.in_(UNFINISHED)).all():
        old.state = "cancelled"
    bc = BotBroadcast(admin_tid=admin_tid, created_at=now)
    db.add(bc)
    db.flush()
    return bc


def own(db, bc_id: str, admin_tid: str, *, now: datetime) -> BotBroadcast | None:
    bc = db.get(BotBroadcast, str(bc_id or ""))
    if bc is None or bc.admin_tid != admin_tid or now - bc.created_at > TTL:
        return None
    return bc


def awaiting_text(db, admin_tid: str, *, now: datetime) -> BotBroadcast | None:
    return (db.query(BotBroadcast)
            .filter(BotBroadcast.admin_tid == admin_tid, BotBroadcast.state == "text",
                    BotBroadcast.created_at >= now - TTL)
            .order_by(BotBroadcast.created_at.desc()).first())


def books(db) -> list[ScriptBook]:
    return db.query(ScriptBook).order_by(ScriptBook.created_at.asc()).all()


def find_book(db, prefix: str) -> ScriptBook | None:
    prefix = str(prefix or "")
    exact = db.get(ScriptBook, prefix) if prefix else None
    if exact is not None:
        return exact
    if len(prefix) < 6:
        return None
    found = db.query(ScriptBook).filter(ScriptBook.id.like(f"{prefix}%")).all()
    return found[0] if len(found) == 1 else None


def header(db, bc: BotBroadcast) -> str:
    if bc.scope == "book":
        book = db.get(ScriptBook, bc.book_id)
        return f"📢 {getattr(book, 'display_title', '') or getattr(book, 'title', '') or studio.name()}"
    return f"📢 {studio.name()} — всем дикторам"


def recipients(db, bc: BotBroadcast) -> list[tuple[str, str]]:
    """(имя, chat_id) — каждый человек один раз; без Telegram — chat_id пустой."""
    if bc.scope == "book":
        states = ["approved", "proposed"] if bc.include_proposed else ["approved"]
        user_ids = {uid for (uid,) in db.query(DictorAssignment.user_id)
                    .filter(DictorAssignment.book_id == bc.book_id, DictorAssignment.state.in_(states)).all()}
    else:
        user_ids = {uid for (uid,) in db.query(UserRole.user_id).filter(UserRole.role == "dictor").all()}
    if not user_ids:
        return []
    users = db.query(User).filter(User.id.in_(sorted(user_ids)), User.is_active == "true").all()
    chats = {row.user_id: str(row.telegram_user_id or "")
             for row in db.query(TelegramAuthAccount).filter(TelegramAuthAccount.user_id.in_(sorted(user_ids)),
                                                             TelegramAuthAccount.is_active == "true").all()}
    # Учётка `tg_<id>` со строкой белого списка без привязки — тот же человек (как в `_person`).
    unlinked = {str(row.telegram_user_id or "") for row in db.query(TelegramAuthAccount).filter(
        TelegramAuthAccount.is_active == "true", TelegramAuthAccount.user_id == "").all()}
    for user in users:
        login = str(user.login or "")
        if user.id not in chats and login.startswith("tg_") and login[3:] in unlinked:
            chats[user.id] = login[3:]
    out = [(str(u.display_name or ""), chats.get(u.id, "")) for u in users]
    return sorted(out, key=lambda item: item[0].lower().replace("ё", "е"))


def deliver(db, bc_id: str, *, send, now: datetime | None = None) -> str:
    """Разослать подтверждённую рассылку. Возвращает итог для админа.

    Журнал со списком адресатов пишется ДО отправки, а кому дошло — после каждого письма
    (ревью 01.10): рестарт посреди рассылки не должен оставить «кому-то ушло, а кому —
    неизвестно». Перевод в `sent` делает нажатие «Отправить», до этой функции.
    """
    now = now or utcnow_naive()
    bc = db.get(BotBroadcast, str(bc_id or ""))
    if bc is None:
        return "Рассылка не найдена."
    text = f"{header(db, bc)}\n\n{bc.text}"
    found = recipients(db, bc)
    progress = {"delivered": [], "failed": [], "no_telegram": [name for name, chat in found if not chat]}
    audit = AuditLog(user_id="", entity_type="broadcast", entity_id=bc.id, action="bot_broadcast", created_at=now,
                     payload_json=json.dumps({"scope": bc.scope, "book_id": bc.book_id, "text": bc.text,
                                              "recipients": [name for name, _ in found]}, ensure_ascii=False))
    db.add(audit)
    bc.sent_at = now
    bc.failed_json = json.dumps(progress, ensure_ascii=False)
    db.commit()
    for name, chat in found:
        if not chat:
            continue
        try:
            ok = bool(send(chat, text))
        except Exception:  # noqa: BLE001 — один адресат не срывает остальных
            ok = False
        progress["delivered" if ok else "failed"].append(name)
        bc.sent_count = len(progress["delivered"])
        bc.failed_json = json.dumps(progress, ensure_ascii=False)
        db.commit()
    lines = [f"Рассылка «{header(db, bc)[2:]}» — Ушло: {len(progress['delivered'])}."]
    if progress["failed"]:
        lines.append(f"Не дошло: {', '.join(progress['failed'])}")
    if progress["no_telegram"]:
        lines.append(f"Без Telegram: {', '.join(progress['no_telegram'])}")
    return "\n".join(lines)
