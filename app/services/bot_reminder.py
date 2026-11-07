"""Кнопка «⏰ Напомнить» в боте — массовое напоминание о записи (решение владельца 05.10).

Писать каждому лично владелец не хочет, а слепая рассылка задела бы тех, кто уже
записывается. Поэтому адресаты собираются из открытых сроков ролей (`RoleDeadline`,
закрываются сами, когда роль сдана целиком), и в письмо попадают только роли, к которым
диктор ещё не приступил — сдано 0 глав: кто начал, тот в работе (решение владельца).
Одно письмо на человека со всеми его ненаписанными ролями выбранной книги (или всех).

Строка разговора — та же `BotBroadcast` (scope «remind»): у неё уже есть подтверждение,
атомарный захват от двойного нажатия и журнал.
"""
from __future__ import annotations

from app.services.studio_calendar import studio_timezone

import json
from dataclasses import dataclass, field
from datetime import datetime

from app.models import AuditLog, BotBroadcast, Character, RoleDeadline, ScriptBook
from app.services import studio
from app.services.role_deadlines import chat_for, format_due, role_progress, same_person
from app.time_utils import utcnow_naive

SCOPE = "remind"


@dataclass
class RoleLine:
    book_title: str
    role: str
    chapters: int
    due_at: datetime


@dataclass
class Target:
    actor: str
    chat: str
    roles: list[RoleLine] = field(default_factory=list)


def _chapters_word(n: int) -> str:
    return f"{n} {'chapter' if n == 1 else 'chapters'}"


def dictors_word(n: int) -> str:
    """«1 диктору», «2 дикторам», «21 диктору»."""
    return f"{n} {'voice actor' if n == 1 else 'voice actors'}"


def targets(db, *, book_id: str) -> list[Target]:
    """Кому напомнить и о каких ролях. `book_id` пустой — все книги."""
    query = db.query(RoleDeadline).filter(RoleDeadline.closed_at.is_(None), RoleDeadline.kind == "role")
    if book_id:
        query = query.filter(RoleDeadline.book_id == book_id)
    # короткое название, без автора: «Учебновы - "Карта Приозерья"» в «ёлочках» двоило бы кавычки
    titles = {b.id: str(b.title or b.display_title or "") for b in db.query(ScriptBook).all()}
    voiced = {b.id for b in db.query(ScriptBook).filter(ScriptBook.voiced_at.isnot(None))}
    found: list[Target] = []
    for row in query.order_by(RoleDeadline.created_at.asc()).all():
        if row.book_id in voiced:
            continue  # по озвученной книге не напоминаем, даже если срок не успели закрыть
        character = db.get(Character, row.character_id)
        if character is None:
            continue
        done, total = role_progress(db, character, row.actor_name)
        if total == 0 or done > 0:
            continue  # ни одной главы роли — напоминать не о чем; начал — в работе
        target = next((t for t in found if same_person(t.actor, row.actor_name)), None)
        if target is None:
            target = Target(actor=str(row.actor_name or ""), chat=chat_for(db, row.actor_name))
            found.append(target)
        target.roles.append(RoleLine(book_title=titles.get(row.book_id, ""), role=str(character.name or ""),
                                     chapters=total, due_at=row.due_at))
    return sorted(found, key=lambda t: t.actor.lower().replace("ё", "е"))


def reminder_text(target: Target, *, timezone: str = "") -> str:
    """Письмо диктору: книги, роли, сколько глав, срок. На «вы», без восклицаний."""
    lines = [f"🎙 {studio.name()} — recording reminder", ""]
    by_book: dict[str, list[RoleLine]] = {}
    for role in target.roles:
        by_book.setdefault(role.book_title, []).append(role)
    for title, roles in sorted(by_book.items(), key=lambda item: item[0].lower()):
        lines.append(f"«{title}»:")
        lines += [f"• «{r.role}» — {_chapters_word(r.chapters)}" for r in roles]
    due = min(r.due_at for r in target.roles)
    lines += ["", f"Deadline: {format_due(due, kind='role', timezone=timezone)}.",
              f"Upload recordings at {studio.site_host()}. For the book currently being recorded, "
              "you can also send files to the bot. If you need help or more time, "
              f"{studio.write_to()}."]
    return "\n".join(lines)


def header(db, bc: BotBroadcast) -> str:
    book = db.get(ScriptBook, bc.book_id) if bc.book_id else None
    return f"⏰ {book.display_title or book.title}" if book is not None else "⏰ All books"


def deliver(db, bc_id: str, *, send, now: datetime | None = None) -> str:
    """Разослать подтверждённое напоминание. Возвращает итог для админа.

    Как у рассылки: журнал со списком адресатов — до отправки, отметка «кому ушло» — после
    каждого письма, чтобы рестарт посреди не оставил неизвестности. Адресаты пересобираются
    сейчас: кто успел начать запись между предпросмотром и «Отправить», письма не получит.
    """
    now = now or utcnow_naive()
    bc = db.get(BotBroadcast, str(bc_id or ""))
    if bc is None:
        return "Reminder not found."
    found = targets(db, book_id=bc.book_id)
    progress = {"delivered": [], "failed": [], "no_telegram": [t.actor for t in found if not t.chat]}
    db.add(AuditLog(user_id="", entity_type="broadcast", entity_id=bc.id, action="bot_reminder", created_at=now,
                    payload_json=json.dumps({"book_id": bc.book_id, "recipients": [t.actor for t in found]},
                                            ensure_ascii=False)))
    bc.sent_at = now
    bc.failed_json = json.dumps(progress, ensure_ascii=False)
    db.commit()
    for target in found:
        if not target.chat:
            continue
        try:
            ok = bool(send(target.chat, reminder_text(target, timezone=studio_timezone(db))))
        except Exception:  # noqa: BLE001 — один адресат не срывает остальных
            ok = False
        progress["delivered" if ok else "failed"].append(target.actor)
        bc.sent_count = len(progress["delivered"])
        bc.failed_json = json.dumps(progress, ensure_ascii=False)
        db.commit()
    lines = [f"Reminder «{header(db, bc)[2:]}» — sent: {len(progress['delivered'])}."]
    if progress["failed"]:
        lines.append(f"Not delivered: {', '.join(progress['failed'])}")
    if progress["no_telegram"]:
        lines.append(f"Without Telegram: {', '.join(progress['no_telegram'])} — please contact them directly.")
    return "\n".join(lines)
