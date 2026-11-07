"""Что бот отвечает на входящее. Чистая логика: в сеть не ходит, возвращает вызовы Bot API.

Человек — это `from.id`, его ставит Telegram. Учётка — только по привязке строки белого
списка или по логину `tg_<id>`, который заводит импорт голосов. Сверки по имени здесь нет
нарочно (в отличие от входа через виджет): бот выдаёт пароль, и тёзка с привязкой «по
имени» получил бы чужую учётку, а хозяин остался бы без пароля. Бот учёток не заводит и
не привязывает. Вызывающий коммитит базу и отправляет ответы.
"""
from __future__ import annotations

import calendar
import html
from dataclasses import dataclass, field
from datetime import datetime

from app.models import TelegramAuthAccount, User, UserRole
from app.config import settings
from app.services.studio_calendar import studio_timezone
from app.services.bot_out import DD_PREFIX, DL_PREFIX, DX_PREFIX, Outgoing, send as _send
from app.services import audition_card, bot_agent_takes, bot_agent_uploads, bot_broadcast, bot_demos, bot_contacts, bot_deadlines, bot_intro, bot_reminder, bot_status, bot_studio, bot_takes, bot_uploads, onboarding, session_template, studio
from app.services.login_identity import TELEGRAM_PASSWORD_HASH, account_can_sign_in
from app.services.user_admin import (
    TELEGRAM_LOGIN_PREFIX, BOT_PASSWORD_LIMIT_PER_HOUR, bot_passwords_issued_last_hour, issue_password_by_bot,
)

BUTTON_TEXT = "🔑 Login and password"
CONFIRM_PREFIX = "pwreset:"
CONFIRM_TTL_SECONDS = 600

def greeting() -> str:
    """Имя студии и адрес сайта — из настроек в момент письма, а не при импорте модуля."""
    return (f"You are connected to {studio.name()}. Role assignments and lines to record again will arrive here. "
            f"Sign in at {studio.site_host()} → Sign in with Telegram.")
NOT_OPEN = "You are connected. Website access is not open yet; we will let you know when it opens."
NOT_OPEN_SHORT = "Website access is not open yet; we will let you know when it opens."
ADMIN_ONLY_SITE = "The administrator password can only be changed on the website."
ASK_RESET = "You already have a password. Issue a new one? The old one will stop working."
STALE = "This button has expired. Select Login and password again."
TOO_OFTEN = "Too many requests. Try again in an hour."
ONLY_THIS = ("I send assignments and provide website access. Commands: /status for your roles, auditions and deadlines; "
             "/help for how we work.")
ASK_NAME = "What is your name? Enter your surname followed by your given name, for example: Smith Alex."
ASK_AGAIN = "I could not read that name. Enter your surname followed by your given name, for example: Smith Alex."
INTRO_STALE = "Open the invitation link again; this question has expired."

HOW_BUTTON = "ℹ️ How we work"
WELCOME = (
    f"🎙 Welcome to {studio.name()}.\n\n"
    "How we work:\n\n"
    f"1. <b>Access.</b> Sign in at {studio.site_host()} with Telegram, or select "
    "🔑 Login and password here in the bot.\n"
    "2. <b>Auditions are by invitation.</b> Please wait for an invitation with the role, book and deadline before recording. "
    "You can send the audition here as an audio file or voice message.\n"
    "3. <b>Approval.</b> If selected, you will receive a role approval and recording deadline. "
    "Then record the role chapter by chapter.\n"
    "4. <b>Recording and alignment.</b> Upload chapters on the website or send a file here for the book currently being recorded. "
    "You can send one chapter or the entire role in one file, with any filename; the bot splits it into chapters. "
    "Takes must be WAV or FLAC; MP3 is accepted for auditions. The system compares the recording with the text "
    "and shows line coverage. Record any missing lines.\n\n"
    f"Questions: {studio.write_to()}."
)
STATUS_BUTTON = "📋 My status"
STUDIO_BUTTON = "🏢 Studio"
OVERVIEW_ROLES = frozenset({"admin", "agent", "author"})
KEYBOARD = {"keyboard": [[{"text": BUTTON_TEXT}, {"text": HOW_BUTTON}], [{"text": STATUS_BUTTON}]],
            "resize_keyboard": True, "is_persistent": True}
STAFF_KEYBOARD = {"keyboard": KEYBOARD["keyboard"] + [[{"text": STUDIO_BUTTON}]],
                  "resize_keyboard": True, "is_persistent": True}
BROADCAST_BUTTON = "📢 Broadcast"
REMIND_BUTTON = "⏰ Remind"
LEGACY_BUTTONS = {
    "🔑 Логин и пароль": BUTTON_TEXT, "ℹ️ Как мы работаем": HOW_BUTTON,
    "📋 Мой статус": STATUS_BUTTON, "🏢 Студия": STUDIO_BUTTON,
    "📢 Рассылка": BROADCAST_BUTTON, "⏰ Напомнить": REMIND_BUTTON,
}
ADMIN_KEYBOARD = {"keyboard": [[{"text": BROADCAST_BUTTON}, {"text": REMIND_BUTTON}],
                               [{"text": STUDIO_BUTTON}, {"text": STATUS_BUTTON}]],
                  "resize_keyboard": True, "is_persistent": True}
BC_PREFIX = "bc:"
RM_PREFIX = "rm:"
NO_KEYBOARD = {"remove_keyboard": True}


def _person(db, telegram_user_id: str) -> tuple[str, User | None]:
    """("none" | "admin" | "ok", учётка)."""
    row = (db.query(TelegramAuthAccount)
           .filter(TelegramAuthAccount.telegram_user_id == telegram_user_id,
                   TelegramAuthAccount.is_active == "true").first())
    if row is None:
        return "none", None
    linked_id = str(row.user_id or "").strip()
    user = db.get(User, linked_id) if linked_id else None
    if user is None:
        user = db.query(User).filter(User.login == f"{TELEGRAM_LOGIN_PREFIX}{telegram_user_id}").first()
    if not account_can_sign_in(user):
        return "none", None
    roles = {r.role for r in db.query(UserRole).filter(UserRole.user_id == user.id)}
    return ("admin" if "admin" in roles else "ok"), user


def _roles(db, tid: str, user: User | None) -> set[str]:
    """Роли человека. Агента ищем и в записи телеграм-доступа: роль живёт в двух местах,
    как в `agent_chat_ids` (у Инги в `user_roles` может не быть `agent`). Других ролей
    оттуда не берём: там стартовая роль, экран учёток её не переписывает, и разжалованный
    «author» иначе сохранил бы обзор студии (ревью 05.10)."""
    roles = {r.role for r in db.query(UserRole).filter(UserRole.user_id == user.id)} if user is not None else set()
    row = (db.query(TelegramAuthAccount)
           .filter(TelegramAuthAccount.telegram_user_id == tid, TelegramAuthAccount.is_active == "true").first())
    if row is not None and str(row.role or "").strip() == "agent":
        roles.add("agent")
    return roles


def _sees_as_staff(roles: set[str]) -> bool:
    """Роль агента не должна отнимать права студии у администратора или автора."""
    return bool(roles & {"admin", "author"})


def _can_upload_for_others(roles: set[str]) -> bool:
    """Администратор сдаёт чужие дубли, даже когда сам назначен на роли книги.

    Раньше он попадал в поток диктора и видел только свои назначения. Автор с ролью
    агента остаётся в прежнем потоке: разрешение расширяем только администратору.
    """
    return "admin" in roles or ("agent" in roles and not _sees_as_staff(roles))


def _sees_overview(roles: set[str]) -> bool:
    return bool(roles & OVERVIEW_ROLES)


def _keyboard(kind: str, roles: set[str]) -> dict | None:
    if kind == "admin":
        return ADMIN_KEYBOARD
    if kind == "ok":
        return STAFF_KEYBOARD if _sees_overview(roles) else KEYBOARD
    return None


def _issue(db, chat_id, user: User, now: datetime, markup: dict | None = None) -> Outgoing:
    if bot_passwords_issued_last_hour(db, user.id, now=now) >= BOT_PASSWORD_LIMIT_PER_HOUR:
        return _send(chat_id, TOO_OFTEN)
    issued = issue_password_by_bot(db, user.id, now=now)
    text = (f"Login: <code>{html.escape(issued['login'])}</code>\n"
            f"Password: <code>{html.escape(issued['password'])}</code>\n"
            f"Sign in: {studio.site_url('/app/login')}\n\n"
            "Keep your password. If you forget it, select the button again.")
    return _send(chat_id, text, markup or KEYBOARD, html_mode=True)


def _confirmation_fresh(data: str, now_ts: int) -> bool:
    try:
        made = int(data[len(CONFIRM_PREFIX):])
    except ValueError:
        return False
    return 0 <= now_ts - made <= CONFIRM_TTL_SECONDS


def _introduce(db, chat_id, tid: str, text: str, now: datetime) -> list[Outgoing]:
    if not bot_intro.may_introduce(db, tid):
        # Учётку тем временем завёл импорт или владелец — вторую не делаем.
        return [_send(chat_id, NOT_OPEN_SHORT, NO_KEYBOARD)]
    name = bot_intro.clean_name(text)
    if not name:
        return [_send(chat_id, ASK_AGAIN)]
    made = bot_intro.create_dictor(db, tid, name, now=now)
    out = [_send(chat_id, f"Ready, {name}! Sign in: {studio.site_host()} → Sign in with Telegram.", KEYBOARD),
           _send(chat_id, WELCOME, KEYBOARD, html_mode=True),
           Outgoing("enqueue_intro", {"telegram_user_id": tid})]
    owner = str(settings.owner_telegram_id or "").strip()
    notes = []
    if made["twins"]:
        notes.append(f"similar accounts: {', '.join(made['twins'])} — merge them on the Users page if they belong to the same person")
    if made["cast"]:
        roles = "; ".join(f"{m['role']} («{m['book']}»)" for m in made["cast"])
        notes.append(f"name found in the cast: {roles} — these roles now belong to the new account")
    if notes and owner:
        out.append(_send(owner, f"Bot: introduced as {name} (account tg_{tid}). " + ". ".join(notes) + "."))
    return out


def _bc_cancel_row(bc_id: str) -> list[dict]:
    return [{"text": "Cancel", "callback_data": f"{BC_PREFIX}x:{bc_id}"}]


def _broadcast_start(db, chat_id, tid: str, now: datetime) -> list[Outgoing]:
    audition_card.drop_comment_wait(db, tid)
    bc = bot_broadcast.start(db, tid, now=now)
    rows = [[{"text": str(b.display_title or b.title or "")[:60], "callback_data": f"{BC_PREFIX}b:{bc.id}:{b.id[:12]}"}]
            for b in bot_broadcast.books(db)]
    rows.append([{"text": "All voice actors", "callback_data": f"{BC_PREFIX}all:{bc.id}"}])
    rows.append(_bc_cancel_row(bc.id))
    return [_send(chat_id, "Who should receive the broadcast? Select a book to contact its cast, or all voice actors.",
                  {"inline_keyboard": rows})]


def _broadcast_ask_text(db, chat_id, bc) -> Outgoing:
    bc.state = "text"
    count = len(bot_broadcast.recipients(db, bc))
    return _send(chat_id, f"{bot_broadcast.header(db, bc)} — {bot_broadcast.people(count)}.\n"
                          "Enter the broadcast as one text message, or select Cancel.",
                 {"inline_keyboard": [_bc_cancel_row(bc.id)]})


def _broadcast_preview(db, chat_id, bc, text: str) -> list[Outgoing]:
    body = text.strip()
    full = f"{bot_broadcast.header(db, bc)}\n\n{body}"
    if len(full.encode("utf-16-le")) // 2 > bot_broadcast.TELEGRAM_LIMIT:
        # Telegram считает 4096 в единицах UTF-16; обрезать молча нельзя — смысл потеряется
        return [_send(chat_id, f"The text is too long for one Telegram message. Shorten it "
                               f"to about {bot_broadcast.TELEGRAM_LIMIT - 100} characters and send it again.")]
    bc.text = body
    bc.state = "confirm"
    found = bot_broadcast.recipients(db, bc)
    with_tg = [name for name, chat in found if chat]
    without = [name for name, chat in found if not chat]
    summary = f"Telegram recipients: {len(with_tg)}."
    if without:
        summary += f" Without Telegram: {', '.join(without)} — please contact them directly."
    buttons = {"inline_keyboard": [[{"text": "Send", "callback_data": f"{BC_PREFIX}s:{bc.id}"}],
                                   _bc_cancel_row(bc.id)]}
    return [_send(chat_id, f"{bot_broadcast.header(db, bc)}\n\n{bc.text}"),
            _send(chat_id, summary.replace("Without Telegram", "without Telegram"), buttons)]


def _broadcast_press(db, chat_id, tid: str, data: str, now: datetime) -> list[Outgoing]:
    out = _broadcast_step(db, chat_id, tid, data, now)
    db.flush()  # шаг рассылки виден следующему сообщению в той же сессии
    return out


def _broadcast_step(db, chat_id, tid: str, data: str, now: datetime) -> list[Outgoing]:
    parts = data[len(BC_PREFIX):].split(":")
    action, bc_id = parts[0], (parts[1] if len(parts) > 1 else "")
    bc = bot_broadcast.own(db, bc_id, tid, now=now)
    if bc is None or bc.state in ("sent", "cancelled"):
        return [_send(chat_id, "This broadcast is closed. Start again with 📢 Broadcast.")] if bc is None or bc.state == "cancelled" else []
    if action == "x":
        bc.state = "cancelled"
        return [_send(chat_id, "Broadcast cancelled.", ADMIN_KEYBOARD)]
    if action == "b" and bc.state == "choose":
        book = bot_broadcast.find_book(db, parts[2] if len(parts) > 2 else "")
        if book is None:
            return [_send(chat_id, "Book not found. Start again.")]
        bc.scope, bc.book_id, bc.state = "book", book.id, "audience"
        rows = [[{"text": "Approved actors", "callback_data": f"{BC_PREFIX}a:{bc.id}:0"}],
                [{"text": "+ invited to audition", "callback_data": f"{BC_PREFIX}a:{bc.id}:1"}],
                _bc_cancel_row(bc.id)]
        return [_send(chat_id, f"{bot_broadcast.header(db, bc)}: send to approved actors only, or include audition invitations? "
                               "The narrator receives it in either case.", {"inline_keyboard": rows})]
    if action == "all" and bc.state == "choose":
        bc.scope = "all"
        return [_broadcast_ask_text(db, chat_id, bc)]
    if action == "a" and bc.state == "audience":
        bc.include_proposed = (parts[2] if len(parts) > 2 else "0") == "1"
        return [_broadcast_ask_text(db, chat_id, bc)]
    if action == "s" and bc.state == "confirm":
        # Захват одной атомарной записью (ревью 01.10): Telegram может прислать два нажатия
        # параллельно, и обе сессии успеют прочесть «confirm» — рассылку получит только та,
        # чья запись действительно перевела строку в «sent».
        from app.models import BotBroadcast

        claimed = (db.query(BotBroadcast)
                   .filter(BotBroadcast.id == bc.id, BotBroadcast.state == "confirm")
                   .update({"state": "sent"}, synchronize_session=False))
        if claimed != 1:
            return []
        db.expire(bc)
        return [_send(chat_id, "Sending…"), Outgoing("broadcast", {"id": bc.id, "admin_chat": chat_id})]
    return []


def _rm_cancel_row(bc_id: str) -> list[dict]:
    return [{"text": "Cancel", "callback_data": f"{RM_PREFIX}x:{bc_id}"}]


def _remind_start(db, chat_id, tid: str, now: datetime) -> list[Outgoing]:
    audition_card.drop_comment_wait(db, tid)
    bc = bot_broadcast.start(db, tid, now=now)
    bc.scope = bot_reminder.SCOPE
    rows = [[{"text": str(b.display_title or b.title or "")[:60], "callback_data": f"{RM_PREFIX}b:{bc.id}:{b.id[:12]}"}]
            for b in bot_broadcast.books(db)]
    rows.append([{"text": "All books", "callback_data": f"{RM_PREFIX}b:{bc.id}:"}])
    rows.append(_rm_cancel_row(bc.id))
    db.flush()
    return [_send(chat_id, "Which book needs a reminder? It will go to approved actors who have not "
                           "started recording their roles.", {"inline_keyboard": rows})]


def _remind_step(db, chat_id, tid: str, data: str, now: datetime) -> list[Outgoing]:
    parts = data[len(RM_PREFIX):].split(":")
    action, bc_id = parts[0], (parts[1] if len(parts) > 1 else "")
    bc = bot_broadcast.own(db, bc_id, tid, now=now)
    if bc is None or bc.scope != bot_reminder.SCOPE or bc.state == "cancelled":
        return [_send(chat_id, "This reminder is closed. Start again with ⏰ Remind.")]
    if bc.state == "sent":
        return []
    if action == "x":
        bc.state = "cancelled"
        return [_send(chat_id, "Reminder cancelled.", ADMIN_KEYBOARD)]
    if action == "b" and bc.state == "choose":
        prefix = parts[2] if len(parts) > 2 else ""
        book = bot_broadcast.find_book(db, prefix) if prefix else None
        if prefix and book is None:
            return [_send(chat_id, "Book not found. Start again.")]
        bc.book_id = book.id if book is not None else ""
        found = bot_reminder.targets(db, book_id=bc.book_id)
        if not found:
            bc.state = "cancelled"
            return [_send(chat_id, f"{bot_reminder.header(db, bc)} has no reminder recipients: all approved actors have already "
                                   "started recording or completed their roles.", ADMIN_KEYBOARD)]
        bc.state = "confirm"
        with_tg = [t for t in found if t.chat]
        without = [t.actor for t in found if not t.chat]
        roles = sum(len(t.roles) for t in with_tg)
        summary = f"{bot_reminder.header(db, bc)}: reminder recipients: {bot_reminder.dictors_word(len(with_tg))} ({roles} roles)."
        if without:
            summary += f"\nWithout Telegram: {', '.join(without)} — please contact them directly."
        out = []
        if with_tg:
            out.append(_send(chat_id, f"Preview — message for «{with_tg[0].actor}»:\n\n{bot_reminder.reminder_text(with_tg[0], timezone=studio_timezone(db))}"))
        buttons = [[{"text": "Send", "callback_data": f"{RM_PREFIX}s:{bc.id}"}]] if with_tg else []
        buttons.append(_rm_cancel_row(bc.id))
        out.append(_send(chat_id, summary.replace("reminder recipients", "Reminder recipients"), {"inline_keyboard": buttons}))
        return out
    if action == "s" and bc.state == "confirm":
        from app.models import BotBroadcast

        claimed = (db.query(BotBroadcast)
                   .filter(BotBroadcast.id == bc.id, BotBroadcast.state == "confirm")
                   .update({"state": "sent"}, synchronize_session=False))
        if claimed != 1:
            return []
        db.expire(bc)
        return [_send(chat_id, "Sending…"), Outgoing("reminder", {"id": bc.id, "admin_chat": chat_id})]
    return []


def handle_update(db, update: dict, *, now: datetime) -> list[Outgoing]:
    now_ts = calendar.timegm(now.timetuple())
    press = update.get("callback_query")
    if isinstance(press, dict):
        chat = (press.get("message") or {}).get("chat") or {}
        if chat.get("type") != "private":
            return []
        tid, chat_id = str((press.get("from") or {}).get("id") or ""), chat.get("id")
        bot_contacts.touch(db, press.get("from") or {}, now=now)
        out = [Outgoing("answerCallbackQuery", {"callback_query_id": press.get("id")})]
        data = str(press.get("data") or "")
        if data.startswith(BC_PREFIX):
            kind, _ = _person(db, tid)
            if kind != "admin":
                return out  # рассылка — только админу; чужое нажатие молча гасим
            return out + _broadcast_press(db, chat_id, tid, data, now)
        if data.startswith(RM_PREFIX):
            kind, _ = _person(db, tid)
            if kind != "admin":
                return out  # напоминание — только админу
            step = _remind_step(db, chat_id, tid, data, now)
            db.flush()
            return out + step
        if data.startswith(DL_PREFIX):
            # нажатия диктора: адресата проверяет сам `bot_deadlines` — по сроку роли
            return out + bot_deadlines.dictor_press(db, tid=tid, chat_id=chat_id, data=data, now=now)
        if data.startswith(bot_demos.DM_PREFIX):
            kind, _ = _person(db, tid)
            if kind != "admin" or not bot_demos.is_owner(tid):
                return out  # приём демо — только владельцу
            step = bot_demos.press(db, chat_id=chat_id, data=data, now=now,
                                   message_id=(press.get("message") or {}).get("message_id"))
            db.flush()
            return out + step
        if data.startswith(bot_takes.BT_PREFIX):
            kind, user = _person(db, tid)
            if kind == "none":
                return out
            step = bot_takes.press(db, tid=tid, chat_id=chat_id, data=data, user=user, now=now)
            db.flush()
            return out + step
        if data.startswith(bot_agent_takes.AT_PREFIX):
            kind, user = _person(db, tid)
            roles = _roles(db, tid, user)
            if kind == "none" or not _can_upload_for_others(roles):
                return out
            step = bot_agent_takes.press(db, tid=tid, chat_id=chat_id, data=data, now=now)
            db.flush()
            return out + step
        if data.startswith(bot_agent_uploads.BA_PREFIX):
            kind, user = _person(db, tid)
            if kind == "none" or not (_roles(db, tid, user) & {"admin", "agent"}):
                return out  # пробы за подопечных — администратору и агенту
            step = bot_agent_uploads.press(db, tid=tid, chat_id=chat_id, data=data, now=now)
            db.flush()
            return out + step
        if data.startswith(bot_uploads.BU_PREFIX):
            kind, user = _person(db, tid)
            if kind == "none":
                return out
            step = bot_uploads.press(db, tid=tid, chat_id=chat_id, data=data, user=user, now=now)
            db.flush()
            return out + step
        if data.startswith(audition_card.AC_PREFIX):
            kind, user = _person(db, tid)
            if kind == "none":
                return out
            message_id = (press.get("message") or {}).get("message_id")
            step = audition_card.press(db, chat_id=chat_id, message_id=message_id, data=data, user=user,
                                       roles=_roles(db, tid, user), now=now, tid=tid)
            db.flush()
            return out + step
        if data.startswith((DD_PREFIX, DX_PREFIX)):
            kind, user = _person(db, tid)
            if kind == "none":
                return out
            message_id = (press.get("message") or {}).get("message_id")
            return out + bot_deadlines.staff_press(db, tid=tid, chat_id=chat_id, message_id=message_id, data=data,
                                                   user=user, roles=_roles(db, tid, user), now=now)
        if data.startswith(bot_studio.DC_PREFIX):
            kind, user = _person(db, tid)
            if kind == "none" or not _sees_overview(_roles(db, tid, user)):
                return out  # карточки — только админу, агенту и автору
            text_out = bot_studio.card_by_pick(db, data, now=now)
            return out + ([_send(chat_id, text_out, html_mode=True)] if text_out else [])
        if data.startswith(bot_studio.SS_PREFIX):
            kind, user = _person(db, tid)
            if kind == "none" or not _sees_overview(_roles(db, tid, user)):
                return out  # обзор — только админу, агенту и автору; чужое нажатие молча гасим
            text_out = bot_studio.section(db, data[len(bot_studio.SS_PREFIX):], now=now)
            return out + ([_send(chat_id, text_out, html_mode=True)] if text_out else [])
        if not data.startswith(CONFIRM_PREFIX) or not _confirmation_fresh(data, now_ts):
            return out + [_send(chat_id, STALE)]
        kind, user = _person(db, tid)
        if kind == "none":
            return out + [_send(chat_id, NOT_OPEN_SHORT, NO_KEYBOARD)]
        if kind == "admin":
            return out + [_send(chat_id, ADMIN_ONLY_SITE)]
        return out + [_issue(db, chat_id, user, now)]

    message = update.get("message")
    if not isinstance(message, dict):
        return []
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return []
    tid, chat_id = str((message.get("from") or {}).get("id") or ""), chat.get("id")
    bot_contacts.touch(db, message.get("from") or {}, now=now)
    text = str(message.get("text") or "").strip()
    # Уже отправленная клавиатура остаётся рабочей после обновления языка интерфейса.
    text = LEGACY_BUTTONS.get(text, text)
    kind, user = _person(db, tid)
    roles = _roles(db, tid, user) if kind != "none" else set()
    keyboard = _keyboard(kind, roles)

    # Пересланное владельцем — всегда демо в «Дикторы», никогда не проба: пробы дикторы шлют
    # боту сами (проект Б). Поэтому ветка стоит до приёма проб.
    if kind == "admin" and bot_demos.is_owner(tid) and isinstance(message.get("forward_origin"), dict):
        out = bot_demos.receive_forward(db, chat_id=chat_id, message=message, now=now)
        db.flush()
        return out

    # .sesx от владельца — шаблон пресетов для сессий глав (25.10): не проба и не демо.
    template_doc = session_template.incoming_template(message)
    if kind == "admin" and bot_demos.is_owner(tid) and template_doc is not None:
        return session_template.receive(chat_id, template_doc)

    attached = bot_uploads.incoming_file(message)
    if attached is not None and kind != "none" and _can_upload_for_others(roles):
        # Администратор и агент грузят чужие дубли: актёр уже известен из каста, спрашивать
        # подопечного заново нельзя — иначе запись утверждённой роли становится пробой.
        out = bot_agent_takes.receive(db, tid=tid, chat_id=chat_id, info=attached, now=now)
        if out is None:
            out = bot_agent_uploads.receive(db, tid=tid, chat_id=chat_id, info=attached, now=now)
        db.flush()
        return out
    if attached is not None and kind != "none":
        # Сначала дубль: у человека с утверждённой ролью в книге «сейчас записывается» файл —
        # запись роли (спека 2026-10-28-bot-takes). `None` — ролей нет, дальше пробы как были.
        out = bot_takes.receive(db, tid=tid, chat_id=chat_id, user=user, info=attached, now=now)
        if out is not None:
            db.flush()
            return out
        # проба файлом или голосовым — только по приглашению (пакет C2)
        out = bot_uploads.receive(db, tid=tid, chat_id=chat_id, user=user, info=attached, now=now)
        if out is not None:
            db.flush()
            return out

    if text.startswith("/start"):
        onboarding.mark_reachable(tid)
        if kind == "none":
            if text.split()[1:2] == ["dictor"] and bot_intro.may_introduce(db, tid):
                bot_intro.ask(db, tid, now=now)
                return [_send(chat_id, ASK_NAME, NO_KEYBOARD)]
            return [_send(chat_id, NOT_OPEN, NO_KEYBOARD)]
        if kind == "ok":
            return [_send(chat_id, WELCOME, keyboard, html_mode=True)]
        return [_send(chat_id, greeting(), keyboard)]

    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if text == HOW_BUTTON or command == "/help":
        if kind == "none":
            return [_send(chat_id, NOT_OPEN_SHORT, NO_KEYBOARD)]
        return [_send(chat_id, WELCOME, keyboard, html_mode=True)]

    if command == "/status" or text == STATUS_BUTTON:
        if kind == "none":
            return [_send(chat_id, NOT_OPEN_SHORT, NO_KEYBOARD)]
        text_out = bot_status.status_text(db, tid, user, admin=kind == "admin", now=now)
        return [_send(chat_id, text_out, keyboard, html_mode=True)]

    if (command == "/studiostatus" or text == STUDIO_BUTTON) and _sees_overview(roles):
        text_out, markup = bot_studio.overview(db, now=now)
        return [_send(chat_id, text_out, markup, html_mode=True)]

    if command in ("/dictor", "/диктор") and _sees_overview(roles):
        query = text.split(maxsplit=1)[1] if len(text.split(maxsplit=1)) > 1 else ""
        text_out, markup = bot_studio.dictor_reply(db, query, now=now)
        return [_send(chat_id, text_out, markup, html_mode=True)]

    owner_typed = (kind == "admin" and bot_demos.is_owner(tid) and text and not text.startswith("/")
                   and text not in (BUTTON_TEXT, BROADCAST_BUTTON, REMIND_BUTTON))
    if owner_typed:
        # Реплай на вопрос об имени диктора — всегда ему, раньше любых других ожиданий.
        named = bot_demos.take_name(db, chat_id=chat_id, text=text, reply_to=message.get("reply_to_message"),
                                    now=now, reply_only=True)
        if named is not None:
            db.flush()
            return named

    # Ответ на «✍️ Своими словами» под карточкой пробы — до рассылки: человек только что
    # нажал кнопку комментария, и его текст — про пробу. Начало рассылки ожидание снимает.
    if kind != "none" and text and not text.startswith("/") and text not in (
            BUTTON_TEXT, BROADCAST_BUTTON, REMIND_BUTTON):
        taken = audition_card.take_comment(db, tid=tid, chat_id=chat_id, text=text, user=user, roles=roles, now=now)
        if taken is not None:
            db.flush()
            return taken

    if kind == "admin":
        if text.lower() in ("/кто", "/who"):
            return [_send(chat_id, bot_contacts.who_now(db, now=now), ADMIN_KEYBOARD)]
        if text == BROADCAST_BUTTON:
            return _broadcast_start(db, chat_id, tid, now)
        if text == REMIND_BUTTON:
            return _remind_start(db, chat_id, tid, now)
        typed = text and not text.startswith("/") and text != BUTTON_TEXT  # старая кнопка пароля — не текст
        pending = bot_broadcast.awaiting_text(db, tid, now=now) if typed else None
        if owner_typed:
            # Простой текст — в единственный открытый приём, если он свежее ожидания рассылки.
            named = bot_demos.take_name(db, chat_id=chat_id, text=text, reply_to=None, now=now,
                                        other_since=pending.created_at if pending is not None else None)
            if named is not None:
                db.flush()
                return named
        if pending is not None:
            out = _broadcast_preview(db, chat_id, pending, text)
            db.flush()
            return out

    if kind == "none" and text and not text.startswith("/") and text != BUTTON_TEXT:
        state = bot_intro.intro_state(db, tid, now=now)
        if state == "stale":
            return [_send(chat_id, INTRO_STALE)]
        if state == "fresh":
            return _introduce(db, chat_id, tid, text, now)

    if text == BUTTON_TEXT:
        if kind == "none":
            return [_send(chat_id, NOT_OPEN_SHORT, NO_KEYBOARD)]
        if kind == "admin":
            return [_send(chat_id, ADMIN_ONLY_SITE, ADMIN_KEYBOARD)]
        if str(user.password_hash or "") == TELEGRAM_PASSWORD_HASH:
            return [_issue(db, chat_id, user, now)]
        confirm = {"inline_keyboard": [[{"text": "Yes, issue a new password",
                                         "callback_data": f"{CONFIRM_PREFIX}{now_ts}"}]]}
        return [_send(chat_id, ASK_RESET, confirm)]

    return [_send(chat_id, ONLY_THIS, KEYBOARD if kind == "ok" else ADMIN_KEYBOARD if kind == "admin" else None)]
