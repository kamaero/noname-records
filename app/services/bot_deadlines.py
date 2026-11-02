"""Кнопки сроков в боте (пакет B, спека 2026-10-05-bot-deadline-buttons).

Раньше по молчанию диктора было не понять, занят он или не увидел приглашение, а продление
он просил в личке. Кнопки несут id персонажа, а не срока: письмо о назначении уходит из
ручки каста раньше, чем `sync_book` заведёт срок. Поэтому при нажатии берём открытый срок
роли и проверяем, что нажал тот самый человек (`chat_for` — то же правило, что у писем;
при двусмысленном имени кнопка не работает ни у кого).

Чистая логика: возвращает ответы (`Outgoing`), коммитит вызывающий (вебхук).
"""
from __future__ import annotations

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

STALE_INVITE = "Приглашение уже неактуально."
NOT_YOURS = "Эта кнопка не для вас."
STALE_BUTTON = "Кнопка устарела."


def _audit(db, row: RoleDeadline, action: str, tid: str, now: datetime, **extra) -> None:
    db.add(AuditLog(user_id="", entity_type="role_deadline", entity_id=row.id, action=action, created_at=now,
                    payload_json=json.dumps({"telegram_user_id": tid, **extra}, ensure_ascii=False)))


def _what(db, row: RoleDeadline, *, accusative: bool = False) -> str:
    """«проба «Дракон» («Карта Приозерья»)» / «роль «Убув» («…»)»; `accusative` — «продлить пробу …»."""
    ch = db.get(Character, row.character_id)
    book = db.get(ScriptBook, row.book_id)
    kind = ("пробу" if accusative else "проба") if row.kind == "audition" else "роль"
    return f"{kind} «{getattr(ch, 'name', '')}» («{getattr(book, 'title', '')}»)"


def _due(row: RoleDeadline) -> str:
    return format_due(row.due_at, kind="time" if row.kind == "audition" else "role")


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
            return [send(chat_id, f"Вы уже взяли пробу. Ждём до {_due(row)}.")]
        row.accepted_at = now
        _audit(db, row, "bot_accept", tid, now)
        db.flush()
        # срок мог выйти, пока письмо лежало непрочитанным: «ждём до» прошедшей даты — неправда
        thanks = (f"Спасибо. Ждём пробу до {_due(row)}." if row.due_at > now else
                  "Спасибо. Срок пробы уже вышел — если нужно время, нажмите «⏳ Нужно больше времени».")
        return [send(chat_id, thanks)] + \
            _tell_staff(db, f"✅ {row.actor_name} — {_what(db, row)} взята, срок — до {_due(row)}.")
    if action == "n":
        _close(row, "declined", now)
        row.declined_at = now
        _audit(db, row, "bot_decline", tid, now)
        db.flush()
        buttons = {"inline_keyboard": [[
            {"text": "Снять с роли", "callback_data": f"{DD_PREFIX}r:{character_id}"},
            {"text": "Оставить", "callback_data": f"{DD_PREFIX}k:{character_id}"}]]}
        return [send(chat_id, "Спасибо, что предупредили. Роль передадим другому исполнителю.")] + \
            _tell_staff(db, f"✖️ {row.actor_name} не сможет — {_what(db, row)}.", buttons)
    days_allowed = AUDITION_DAYS if row.kind == "audition" else ROLE_DAYS
    if action == "t":
        word = {1: "+1 день", 2: "+2 дня", 7: "+7 дней", 14: "+14 дней"}
        buttons = {"inline_keyboard": [[{"text": word[d], "callback_data": f"{DL_PREFIX}e{d}:{character_id}"}
                                        for d in days_allowed]]}
        now_due = f"Сейчас срок — до {_due(row)}." if row.due_at > now else f"Срок вышел {_due(row)}."
        return [send(chat_id, f"{now_due} На сколько продлить?", buttons)]
    if action.startswith("e") and action[1:].isdigit():
        days = int(action[1:])
        if days not in days_allowed:
            return [send(chat_id, STALE_BUTTON)]
        if row.extend_request_days:
            return [send(chat_id, "Запрос уже отправлен — ждите ответа здесь.")]
        row.extend_request_days, row.extend_request_at = days, now
        _audit(db, row, "bot_extend_request", tid, now, days=days)
        db.flush()
        # отметка запроса в кнопке: решённый (в том числе на сайте) запрос и новый запрос после
        # него — разные; старая кнопка не должна решать новый
        stamp = calendar.timegm(now.timetuple())
        buttons = {"inline_keyboard": [[
            {"text": f"Продлить на {days} дн.", "callback_data": f"{DX_PREFIX}y:{row.id}:{stamp}"},
            {"text": "Нет", "callback_data": f"{DX_PREFIX}n:{row.id}:{stamp}"}]]}
        owner, _ = _staff_chats(db)
        out = [send(chat_id, "Запрос отправлен — ответим здесь.")]
        if owner:
            out.append(send(owner, f"⏳ {row.actor_name} просит продлить {_what(db, row, accusative=True)} на {days} дн. "
                                   f"Сейчас — до {_due(row)}.", buttons))
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
        return [edit_off(chat_id, message_id), send(chat_id, "Решение уже неактуально.")]
    off = edit_off(chat_id, message_id)
    if action == "k":
        _audit(db, declined, "bot_decline_keep", tid, now)
        db.flush()
        return [off, send(chat_id, "Хорошо, оставляю.")]
    if action != "r":
        return [send(chat_id, STALE_BUTTON)]
    holder, _ = parse_actor_name(str(ch.actor_name or ""))
    if not holder:
        return [off, send(chat_id, f"Роль «{ch.name}» уже свободна.")]
    if not same_person(holder, declined.actor_name):
        return [off, send(chat_id, f"Роль уже у {holder} — ничего не меняю.")]
    was = str(ch.actor_name or "").strip()
    vote = cast_role_vote(db, character=ch, voter_uid=str(user.id), voter_name=str(user.display_name or ""),
                          weight=vote_weight(roles), actor_name="")
    if not vote["changed"]:
        # без слов, выдающих род: перевесить может и автор, и любой проголосовавший
        return [off, send(chat_id, f"Не снято: перевешивает голос {vote['overridden_by'] or 'другого участника'} — "
                                   "решает этот голос.")]
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
        return [off, send(chat_id, f"Роль теперь у {shown} — так решили голоса.")]
    return [off, send(chat_id, f"Снято: роль «{ch.name}» свободна.")]


def _extend_decision(db, *, tid, chat_id, message_id, data, user, roles, now) -> list[Outgoing]:
    if "admin" not in roles:
        return []  # продлевает только админ — как на сайте (решение 01.10)
    action, deadline_id, stamp = (data[len(DX_PREFIX):].split(":") + ["", ""])[:3]
    row = db.get(RoleDeadline, deadline_id)
    off = edit_off(chat_id, message_id)
    if row is None or row.closed_at is not None or not row.extend_request_days or row.extend_request_at is None \
            or str(calendar.timegm(row.extend_request_at.timetuple())) != stamp:
        return [off, send(chat_id, "Запрос уже неактуален.")]
    days = int(row.extend_request_days)
    row.extend_request_days, row.extend_request_at = None, None
    dictor = chat_for(db, row.actor_name)
    if action == "y":
        extend(db, row.id, days=days, by=str(getattr(user, "display_name", "") or ""), now=now)
        _audit(db, row, "bot_extend_yes", tid, now, days=days)
        db.flush()
        ch = db.get(Character, row.character_id)
        book = db.get(ScriptBook, row.book_id)
        out = [off, send(chat_id, f"Продлено до {_due(row)}.")]
        if dictor:
            out.append(send(dictor, extension_notice(row, role=str(getattr(ch, "name", "") or ""),
                                                     book_title=str(getattr(book, "title", "") or ""))))
        return out
    _audit(db, row, "bot_extend_no", tid, now, days=days)
    db.flush()
    out = [off, send(chat_id, "Отказано.")]
    if dictor:
        out.append(send(dictor, f"Продлить срок не получилось. Если нужно обсудить — {studio.write_to()}."))
    return out
