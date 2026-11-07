"""Администратор или агент присылает дубль за актёра из утверждённого каста.

Раньше любой файл агента шёл в пробы и требовал выбрать подопечного. Теперь имя
файла может сразу назвать роль, а без него спрашиваем вид записи и только роль.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from app.models import BotUpload
from app.services import bot_agent_uploads, bot_takes, take_format
from app.services.bot_out import Outgoing, send
from app.services.bot_uploads import CHOICE_TTL
from app.services.studio_settings import active_book
from app.services.telegram import max_download_bytes, size_text
from app.v2.cast_ops import parse_actor_name

AT_PREFIX = "at:"
PAGE = 20


def _roles(db, book) -> list:
    if book is None:
        return []
    return [ch for ch in bot_agent_uploads._cast(db, book.id)
            if parse_actor_name(ch.actor_name)[0] and not parse_actor_name(ch.actor_name)[1]]


def _audition_name(filename: str) -> bool:
    return bool(re.search(r"(?:^|[\s_.-])(?:proba|проба|audition)(?:$|[\s_.-])",
                          Path(str(filename or "")).stem, re.IGNORECASE))


def _cancel(row) -> list[dict]:
    return [{"text": 'Cancel', "callback_data": f"{AT_PREFIX}x:{row.id}"}]


def _queue(db, chat_id, row, ch, now) -> list[Outgoing]:
    if not take_format.allowed_filename(row.file_name, kind=row.kind):
        return [send(chat_id, take_format.REQUIRED)]
    if not bot_takes._claim(db, row.id, ch, now):
        return []
    return bot_takes._queued(db, chat_id, row)


def _role_page(db, chat_id, row, roles, page: int) -> list[Outgoing]:
    page = max(0, min(page, (len(roles) - 1) // PAGE))
    start = page * PAGE
    buttons = [[{"text": f"{ch.name} — {parse_actor_name(ch.actor_name)[0]}"[:60],
                 "callback_data": f"{AT_PREFIX}r:{row.id}:{ch.id}"}]
               for ch in roles[start:start + PAGE]]
    nav = []
    if page:
        nav.append({"text": '‹ Back', "callback_data": f"{AT_PREFIX}p:{row.id}:{page - 1}"})
    if start + PAGE < len(roles):
        nav.append({"text": 'Next ›', "callback_data": f"{AT_PREFIX}p:{row.id}:{page + 1}"})
    return [send(chat_id, 'Which role is this take for? The actor is shown beside the role.',
                 {"inline_keyboard": [*buttons, *([nav] if nav else []), _cancel(row)]})]


def receive(db, *, tid: str, chat_id, info: dict, now: datetime) -> list[Outgoing] | None:
    if info.get("bad") or _audition_name(info.get("file_name", "")):
        return None
    book = active_book(db)
    roles = _roles(db, book)
    limit = max_download_bytes()
    if info["file_size"] > limit:
        return [send(chat_id, f'The file is larger than {size_text(limit)} — upload the recording on the website.')]
    ch = bot_takes._role_from_filename(str(info.get("file_name") or ""), roles, book) if roles else None
    if ch is not None and not take_format.allowed_filename(info.get("file_name", ""), kind=info["kind"]):
        return [send(chat_id, take_format.REQUIRED)]
    earlier = bot_takes.already_received(db, tid=tid, unique=info.get("file_unique_id", ""), now=now)
    if earlier is not None:
        return [send(chat_id, f'This file has already been accepted {earlier.created_at:%d.%m}.')]
    row = BotUpload(telegram_user_id=str(tid), created_at=now, updated_at=now,
                    purpose="take" if ch is not None else "agentpick", book_id=book.id if book is not None else "",
                    **{key: info[key] for key in ("file_id", "file_unique_id", "kind", "mime_type", "file_name",
                                                "file_size", "duration")})
    db.add(row)
    db.flush()
    if ch is not None:
        return _queue(db, chat_id, row, ch, now)
    return [send(chat_id, 'Is this a take or an audition?', {"inline_keyboard": [
        [{"text": 'Take', "callback_data": f"{AT_PREFIX}t:{row.id}"},
         {"text": 'Audition', "callback_data": f"{AT_PREFIX}a:{row.id}"}], _cancel(row)]})]


def press(db, *, tid: str, chat_id, data: str, now: datetime) -> list[Outgoing]:
    action, upload_id, arg = (data[len(AT_PREFIX):].split(":") + ["", ""])[:3]
    row = db.get(BotUpload, upload_id)
    if row is None or row.telegram_user_id != str(tid) or row.purpose not in {"agentpick", "take"}:
        return []
    if row.state != "choose":
        return [send(chat_id, 'This file has already been accepted or cancelled.')]
    if action == "x":
        # Параллельное нажатие «роль» могло уже поставить задачу: отмена не должна
        # переписать `queued` и обещать человеку, что файл не обрабатывается.
        claimed = (db.query(BotUpload).filter(BotUpload.id == row.id, BotUpload.state == "choose")
                   .update({"state": "cancelled", "updated_at": now}, synchronize_session=False))
        db.expire(row)
        return [send(chat_id, 'Cancelled.' if claimed == 1 else 'This file has already been accepted or cancelled.')]
    if row.created_at < now - CHOICE_TTL:
        return [send(chat_id, 'This selection has expired. Send the file again.')]
    if action == "a" and row.purpose == "agentpick":
        if not bot_agent_uploads.wards(db):
            return [send(chat_id, 'No actors are available for an audition yet. Propose an actor in Cast.')]
        claimed = (db.query(BotUpload).filter(BotUpload.id == row.id, BotUpload.state == "choose",
                                             BotUpload.purpose == "agentpick")
                   .update({"purpose": "agent", "updated_at": now}, synchronize_session=False))
        if claimed != 1:
            return []
        db.expire(row)
        return bot_agent_uploads._ward_page(db, chat_id, row, 0)
    book = active_book(db)
    if book is None:
        return [send(chat_id, bot_takes.NO_BOOK)]
    if book.id != row.book_id:
        return [send(chat_id, 'The book currently being recorded has changed. Send the file again.')]
    roles = _roles(db, book)
    if not roles:
        return [send(chat_id, 'This book has no approved roles to record yet.')]
    if not take_format.allowed_filename(row.file_name, kind=row.kind):
        return [send(chat_id, take_format.REQUIRED)]
    if action == "t" and row.purpose == "agentpick":
        claimed = (db.query(BotUpload).filter(BotUpload.id == row.id, BotUpload.state == "choose",
                                             BotUpload.purpose == "agentpick")
                   .update({"purpose": "take", "updated_at": now}, synchronize_session=False))
        if claimed != 1:
            return []
        db.expire(row)
        if len(roles) == 1:
            return _queue(db, chat_id, row, roles[0], now)
        return _role_page(db, chat_id, row, roles, 0)
    if row.purpose != "take":
        return []
    if action == "p":
        return _role_page(db, chat_id, row, roles, int(arg) if arg.isdigit() else 0)
    if action == "r":
        ch = next((ch for ch in roles if ch.id == arg), None)
        if ch is None:
            return [send(chat_id, 'This role is no longer approved. Choose another or send the file again.')]
        return _queue(db, chat_id, row, ch, now)
    return []
