"""Кнопки сроков в боте (пакет B, спека 2026-10-05-bot-deadline-buttons).

Раньше по молчанию диктора было не понять, занят он или не увидел приглашение, а продление
он просил в личке. Кнопки несут id персонажа, а не срока: письмо о назначении уходит из
ручки каста раньше, чем `sync_book` заведёт срок. Поэтому при нажатии берём открытый срок
роли и проверяем, что нажал тот самый человек (`chat_for` — то же правило, что у писем;
при двусмысленном имени кнопка не работает ни у кого).

Чистая логика: возвращает ответы (`Outgoing`), коммитит вызывающий (вебхук).
"""
from __future__ import annotations

from app.services.studio_calendar import studio_timezone

import calendar
import json
from datetime import datetime

from app.config import settings
from app.services import studio
from app.models import AuditLog, Character, RoleDeadline, ScriptBook
from app.services.bot_out import DD_PREFIX, DL_PREFIX, DX_PREFIX, Outgoing, edit_off, send
from app.services.role_deadlines import (
    _close, _latest, chat_for, extend, extension_notice, format_due, open_deadline, same_person,
)
from app.v2.cast_ops import parse_actor_name

AUDITION_DAYS = (1, 2)
ROLE_DAYS = (7, 14)

STALE_INVITE = "This invitation is no longer current."
NOT_YOURS = "This button is for another person."
STALE_BUTTON = "This button has expired."


def _audit(db, row: RoleDeadline, action: str, tid: str, now: datetime, **extra) -> None:
    db.add(AuditLog(user_id="", entity_type="role_deadline", entity_id=row.id, action=action, created_at=now,
                    payload_json=json.dumps({"telegram_user_id": tid, **extra}, ensure_ascii=False)))


def _what(db, row: RoleDeadline, *, accusative: bool = False) -> str:
    """«проба «Дракон» («Карта Приозерья»)» / «роль «Убув» («…»)»; `accusative` — «продлить пробу …»."""
    ch = db.get(Character, row.character_id)
    book = db.get(ScriptBook, row.book_id)
    kind = ("audition" if accusative else "audition") if row.kind == "audition" else "role"
    return f"{kind} «{getattr(ch, 'name', '')}» («{getattr(book, 'title', '')}»)"


def _due(db, row: RoleDeadline) -> str:
    return format_due(row.due_at, kind="time" if row.kind == "audition" else "role", timezone=studio_timezone(db))


def _staff_chats(db) -> tuple[str, list[str]]:
    """Владелец и агенты — им новости о кнопках диктора (агентам без кнопок решений)."""
    from app.services.role_approval import agent_chat_ids

    owner = str(settings.owner_telegram_id or "").strip()
    return owner, [chat for chat in agent_chat_ids(db) if chat != owner]


def _tell_staff(db, text: str, owner_markup: dict | None = None) -> list[Outgoing]:
    owner, agents = _staff_chats(db)
    out = [send(owner, text, owner_markup)] if owner else []
    return out + [send(chat, text) for chat in agents]


def dictor_press(db, *, tid: str, chat_id, data: str, now: datetime) -> list[Outgoing]:
    action, _, character_id = data[len(DL_PREFIX):].partition(":")
    row = open_deadline(db, character_id) if character_id else None
    if row is None:
        return [send(chat_id, STALE_INVITE)]
    if chat_for(db, row.actor_name) != str(tid):
        return [send(chat_id, NOT_YOURS)]
    if action in ("y", "n") and row.kind != "audition":
        return [send(chat_id, STALE_INVITE)]
    if action == "y":
        if row.accepted_at is not None:
            return [send(chat_id, f"You have already accepted the audition. It is due {_due(db, row)}.")]
        row.accepted_at = now
        _audit(db, row, "bot_accept", tid, now)
        db.flush()
        # срок мог выйти, пока письмо лежало непрочитанным: «ждём до» прошедшей даты — неправда
        thanks = (f"Thank you. The audition is due {_due(db, row)}." if row.due_at > now else
                  "Thank you. The audition deadline has passed. Select ⏳ Need more time if needed.")
        return [send(chat_id, thanks)] + \
            _tell_staff(db, f"✅ {row.actor_name} — {_what(db, row)} accepted; due {_due(db, row)}.")
    if action == "n":
        _close(row, "declined", now)
        row.declined_at = now
        _audit(db, row, "bot_decline", tid, now)
        db.flush()
        buttons = {"inline_keyboard": [[
            {"text": "Reassign role", "callback_data": f"{DD_PREFIX}r:{character_id}"},
            {"text": "Keep assignment", "callback_data": f"{DD_PREFIX}k:{character_id}"}]]}
        return [send(chat_id, "Thank you for letting us know. We will reassign the role.")] + \
            _tell_staff(db, f"✖️ {row.actor_name} cannot record — {_what(db, row)}.", buttons)
    days_allowed = AUDITION_DAYS if row.kind == "audition" else ROLE_DAYS
    if action == "t":
        word = {1: "+1 day", 2: "+2 days", 7: "+7 days", 14: "+14 days"}
        buttons = {"inline_keyboard": [[{"text": word[d], "callback_data": f"{DL_PREFIX}e{d}:{character_id}"}
                                        for d in days_allowed]]}
        now_due = f"Current deadline: {_due(db, row)}." if row.due_at > now else f"Deadline passed {_due(db, row)}."
        return [send(chat_id, f"{now_due} How much more time do you need?", buttons)]
    if action.startswith("e") and action[1:].isdigit():
        days = int(action[1:])
        if days not in days_allowed:
            return [send(chat_id, STALE_BUTTON)]
        if row.extend_request_days:
            return [send(chat_id, "Your request has already been sent; the reply will arrive here.")]
        row.extend_request_days, row.extend_request_at = days, now
        _audit(db, row, "bot_extend_request", tid, now, days=days)
        db.flush()
        # отметка запроса в кнопке: решённый (в том числе на сайте) запрос и новый запрос после
        # него — разные; старая кнопка не должна решать новый
        stamp = calendar.timegm(now.timetuple())
        buttons = {"inline_keyboard": [[
            {"text": f"Extend by {days} {'day' if days == 1 else 'days'}", "callback_data": f"{DX_PREFIX}y:{row.id}:{stamp}"},
            {"text": "No", "callback_data": f"{DX_PREFIX}n:{row.id}:{stamp}"}]]}
        owner, _ = _staff_chats(db)
        out = [send(chat_id, "Request sent; the reply will arrive here.")]
        if owner:
            out.append(send(owner, f"⏳ {row.actor_name} asks to extend {_what(db, row, accusative=True)} by {days} {'day' if days == 1 else 'days'}. "
                                   f"Current deadline: {_due(db, row)}.", buttons))
        return out
    return [send(chat_id, STALE_BUTTON)]


DECIDE_REMOVE = frozenset({"admin", "author"})   # вес голоса > 0: агент снять не может


def staff_press(db, *, tid: str, chat_id, message_id, data: str, user, roles: set[str],
                now: datetime) -> list[Outgoing]:
    if data.startswith(DD_PREFIX):
        return _decline_decision(db, tid=tid, chat_id=chat_id, message_id=message_id, data=data, user=user,
                                 roles=roles, now=now)
    return _extend_decision(db, tid=tid, chat_id=chat_id, message_id=message_id, data=data, user=user,
                            roles=roles, now=now)


def _decline_decision(db, *, tid, chat_id, message_id, data, user, roles, now) -> list[Outgoing]:
    from app.services.casting import rebuild_assignments
    from app.services.role_votes import cast_role_vote, vote_weight

    if not roles & DECIDE_REMOVE or user is None:
        return []
    action, _, character_id = data[len(DD_PREFIX):].partition(":")
    ch = db.get(Character, character_id)
    declined = _latest(db, character_id) if ch is not None else None
    if ch is None or declined is None or declined.close_reason != "declined":
        return [edit_off(chat_id, message_id), send(chat_id, "This decision is no longer current.")]
    off = edit_off(chat_id, message_id)
    if action == "k":
        _audit(db, declined, "bot_decline_keep", tid, now)
        db.flush()
        return [off, send(chat_id, "The assignment has been kept.")]
    if action != "r":
        return [send(chat_id, STALE_BUTTON)]
    holder, _ = parse_actor_name(str(ch.actor_name or ""))
    if not holder:
        return [off, send(chat_id, f"Role «{ch.name}» is already available.")]
    if not same_person(holder, declined.actor_name):
        return [off, send(chat_id, f"The role is already assigned to {holder} — no changes made.")]
    was = str(ch.actor_name or "").strip()
    vote = cast_role_vote(db, character=ch, voter_uid=str(user.id), voter_name=str(user.display_name or ""),
                          weight=vote_weight(roles), actor_name="")
    if not vote["changed"]:
        # без слов, выдающих род: перевесить может и автор, и любой проголосовавший
        return [off, send(chat_id, f"Not reassigned: the deciding vote belongs to {vote['overridden_by'] or 'another participant'} — "
                                   "that vote decides.")]
    # тот же путь, что правка поля «Актёр» на сайте: назначения и сроки — по новому касту
    rebuild_assignments(db, ch.book_id)
    _audit(db, declined, "bot_decline_remove", tid, now)
    db.flush()
    winner = str(vote["actor_name"] or "")
    if winner:
        # пересчёт голосов отдал роль третьему (имя в касте стояло мимо голосов) — как на сайте,
        # новому исполнителю уходит письмо (ревью пакета B: иначе он не узнал бы о роли)
        from app.services.role_approval import notify_role_approved, should_notify_actor_change

        if should_notify_actor_change(was, winner):
            book = db.get(ScriptBook, ch.book_id)
            notify_role_approved(db, book_id=str(ch.book_id), book_title=str(getattr(book, "title", "") or ""),
                                 role=str(ch.name or ""), actor_name=winner)
        shown, _ = parse_actor_name(winner)
        return [off, send(chat_id, f"The role is now assigned to {shown} — based on the votes.")]
    return [off, send(chat_id, f"Unassigned: role «{ch.name}» is available.")]


def _extend_decision(db, *, tid, chat_id, message_id, data, user, roles, now) -> list[Outgoing]:
    if "admin" not in roles:
        return []  # продлевает только админ — как на сайте (решение 01.10)
    action, deadline_id, stamp = (data[len(DX_PREFIX):].split(":") + ["", ""])[:3]
    row = db.get(RoleDeadline, deadline_id)
    off = edit_off(chat_id, message_id)
    if row is None or row.closed_at is not None or not row.extend_request_days or row.extend_request_at is None \
            or str(calendar.timegm(row.extend_request_at.timetuple())) != stamp:
        return [off, send(chat_id, "This request is no longer current.")]
    days = int(row.extend_request_days)
    row.extend_request_days, row.extend_request_at = None, None
    dictor = chat_for(db, row.actor_name)
    if action == "y":
        extend(db, row.id, days=days, by=str(getattr(user, "display_name", "") or ""), now=now)
        _audit(db, row, "bot_extend_yes", tid, now, days=days)
        db.flush()
        ch = db.get(Character, row.character_id)
        book = db.get(ScriptBook, row.book_id)
        out = [off, send(chat_id, f"Extended until {_due(db, row)}.")]
        if dictor:
            out.append(send(dictor, extension_notice(row, role=str(getattr(ch, "name", "") or ""),
                                                     book_title=str(getattr(book, "title", "") or ""), timezone=studio_timezone(db))))
        return out
    _audit(db, row, "bot_extend_no", tid, now, days=days)
    db.flush()
    out = [off, send(chat_id, "Declined.")]
    if dictor:
        out.append(send(dictor, f"Could not extend the deadline. To discuss it, {studio.write_to()}."))
    return out
