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
from app.services import bot_broadcast, bot_contacts, bot_intro, onboarding, studio
from app.services.login_identity import TELEGRAM_PASSWORD_HASH, account_can_sign_in
from app.services.user_admin import (
    TELEGRAM_LOGIN_PREFIX, BOT_PASSWORD_LIMIT_PER_HOUR, bot_passwords_issued_last_hour, issue_password_by_bot,
)

BUTTON_TEXT = "🔑 Логин и пароль"
CONFIRM_PREFIX = "pwreset:"
CONFIRM_TTL_SECONDS = 600

def greeting() -> str:
    """Имя студии и адрес сайта — из настроек в момент письма, а не при импорте модуля."""
    return (f"Вы на связи с {studio.name()}. Сюда будут приходить назначения на роли и реплики для "
            f"перезаписи. Вход на сайт: {studio.site_host()} → «Войти через Telegram».")
NOT_OPEN = "Вы на связи. Доступ к сайту пока не открыт — мы напишем, когда откроем."
NOT_OPEN_SHORT = "Доступ к сайту пока не открыт — мы напишем, когда откроем."
ADMIN_ONLY_SITE = "Пароль администратора меняется только на сайте."
ASK_RESET = "У вас уже есть пароль. Выдать новый? Старый перестанет работать."
STALE = "Кнопка устарела — нажмите «Логин и пароль» ещё раз."
TOO_OFTEN = "Слишком часто — попробуйте через час."
ONLY_THIS = "Я умею только присылать задания и выдавать вход на сайт."
ASK_NAME = "Как вас зовут? Напишите фамилию и имя, например: Ветрова Ольга."
ASK_AGAIN = "Не получилось разобрать. Напишите фамилию и имя, например: Ветрова Ольга."
INTRO_STALE = "Нажмите ещё раз ссылку из приглашения — вопрос устарел."

KEYBOARD = {"keyboard": [[{"text": BUTTON_TEXT}]], "resize_keyboard": True, "is_persistent": True}
BROADCAST_BUTTON = "📢 Рассылка"
ADMIN_KEYBOARD = {"keyboard": [[{"text": BROADCAST_BUTTON}]], "resize_keyboard": True, "is_persistent": True}
BC_PREFIX = "bc:"
NO_KEYBOARD = {"remove_keyboard": True}


@dataclass(frozen=True)
class Outgoing:
    method: str
    payload: dict = field(default_factory=dict)


def _send(chat_id, text: str, markup: dict | None = None, *, html_mode: bool = False) -> Outgoing:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if markup is not None:
        payload["reply_markup"] = markup
    if html_mode:
        payload["parse_mode"] = "HTML"
    return Outgoing("sendMessage", payload)


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


def _issue(db, chat_id, user: User, now: datetime) -> Outgoing:
    if bot_passwords_issued_last_hour(db, user.id, now=now) >= BOT_PASSWORD_LIMIT_PER_HOUR:
        return _send(chat_id, TOO_OFTEN)
    issued = issue_password_by_bot(db, user.id, now=now)
    text = (f"Логин: <code>{html.escape(issued['login'])}</code>\n"
            f"Пароль: <code>{html.escape(issued['password'])}</code>\n"
            f"Вход: {studio.site_url('/app/login')}\n\n"
            "Сохраните пароль. Забудете — нажмите кнопку снова.")
    return _send(chat_id, text, KEYBOARD, html_mode=True)


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
    out = [_send(chat_id, f"Готово, {name}! Вход: {studio.site_host()} → «Войти через Telegram».", KEYBOARD),
           Outgoing("enqueue_intro", {"telegram_user_id": tid})]
    owner = str(settings.owner_telegram_id or "").strip()
    notes = []
    if made["twins"]:
        notes.append(f"похожие учётки: {', '.join(made['twins'])} — если это один человек, слейте на экране учёток")
    if made["cast"]:
        roles = "; ".join(f"{m['role']} («{m['book']}»)" for m in made["cast"])
        notes.append(f"имя стоит в касте: {roles} — эти роли теперь у новой учётки")
    if notes and owner:
        out.append(_send(owner, f"Бот: представился {name} (учётка tg_{tid}). " + ". ".join(notes) + "."))
    return out


def _bc_cancel_row(bc_id: str) -> list[dict]:
    return [{"text": "Отмена", "callback_data": f"{BC_PREFIX}x:{bc_id}"}]


def _broadcast_start(db, chat_id, tid: str, now: datetime) -> list[Outgoing]:
    bc = bot_broadcast.start(db, tid, now=now)
    rows = [[{"text": str(b.display_title or b.title or "")[:60], "callback_data": f"{BC_PREFIX}b:{bc.id}:{b.id[:12]}"}]
            for b in bot_broadcast.books(db)]
    rows.append([{"text": "Всем дикторам", "callback_data": f"{BC_PREFIX}all:{bc.id}"}])
    rows.append(_bc_cancel_row(bc.id))
    return [_send(chat_id, "Кому рассылка? Выберите книгу — письмо уйдёт её касту, — или всех дикторов.",
                  {"inline_keyboard": rows})]


def _broadcast_ask_text(db, chat_id, bc) -> Outgoing:
    bc.state = "text"
    count = len(bot_broadcast.recipients(db, bc))
    return _send(chat_id, f"{bot_broadcast.header(db, bc)} — {bot_broadcast.people(count)}.\n"
                          "Напишите текст рассылки одним сообщением (только текст; передумали — «Отмена»).",
                 {"inline_keyboard": [_bc_cancel_row(bc.id)]})


def _broadcast_preview(db, chat_id, bc, text: str) -> list[Outgoing]:
    body = text.strip()
    full = f"{bot_broadcast.header(db, bc)}\n\n{body}"
    if len(full.encode("utf-16-le")) // 2 > bot_broadcast.TELEGRAM_LIMIT:
        # Telegram считает 4096 в единицах UTF-16; обрезать молча нельзя — смысл потеряется
        return [_send(chat_id, f"Текст слишком длинный для одного сообщения Telegram — сократите "
                               f"до ~{bot_broadcast.TELEGRAM_LIMIT - 100} знаков и пришлите снова.")]
    bc.text = body
    bc.state = "confirm"
    found = bot_broadcast.recipients(db, bc)
    with_tg = [name for name, chat in found if chat]
    without = [name for name, chat in found if not chat]
    summary = f"Получат в Telegram: {len(with_tg)}."
    if without:
        summary += f" Без Telegram: {', '.join(without)} — им напишите сами."
    buttons = {"inline_keyboard": [[{"text": "Отправить", "callback_data": f"{BC_PREFIX}s:{bc.id}"}],
                                   _bc_cancel_row(bc.id)]}
    return [_send(chat_id, f"{bot_broadcast.header(db, bc)}\n\n{bc.text}"),
            _send(chat_id, summary.replace("Без Telegram", "без Telegram"), buttons)]


def _broadcast_press(db, chat_id, tid: str, data: str, now: datetime) -> list[Outgoing]:
    out = _broadcast_step(db, chat_id, tid, data, now)
    db.flush()  # шаг рассылки виден следующему сообщению в той же сессии
    return out


def _broadcast_step(db, chat_id, tid: str, data: str, now: datetime) -> list[Outgoing]:
    parts = data[len(BC_PREFIX):].split(":")
    action, bc_id = parts[0], (parts[1] if len(parts) > 1 else "")
    bc = bot_broadcast.own(db, bc_id, tid, now=now)
    if bc is None or bc.state in ("sent", "cancelled"):
        return [_send(chat_id, "Эта рассылка уже закрыта. Начните заново: «📢 Рассылка».")] if bc is None or bc.state == "cancelled" else []
    if action == "x":
        bc.state = "cancelled"
        return [_send(chat_id, "Рассылка отменена.", ADMIN_KEYBOARD)]
    if action == "b" and bc.state == "choose":
        book = bot_broadcast.find_book(db, parts[2] if len(parts) > 2 else "")
        if book is None:
            return [_send(chat_id, "Книга не нашлась — начните заново.")]
        bc.scope, bc.book_id, bc.state = "book", book.id, "audience"
        rows = [[{"text": "Утверждённым", "callback_data": f"{BC_PREFIX}a:{bc.id}:0"}],
                [{"text": "+ позванным на пробу", "callback_data": f"{BC_PREFIX}a:{bc.id}:1"}],
                _bc_cancel_row(bc.id)]
        return [_send(chat_id, f"{bot_broadcast.header(db, bc)}: кому — утверждённым или ещё и позванным на пробу? "
                               "Рассказчик книги получит в обоих случаях.", {"inline_keyboard": rows})]
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
        return [_send(chat_id, "Отправляю…"), Outgoing("broadcast", {"id": bc.id, "admin_chat": chat_id})]
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
    kind, user = _person(db, tid)

    if text.startswith("/start"):
        onboarding.mark_reachable(tid)
        if kind == "none":
            if text.split()[1:2] == ["dictor"] and bot_intro.may_introduce(db, tid):
                bot_intro.ask(db, tid, now=now)
                return [_send(chat_id, ASK_NAME, NO_KEYBOARD)]
            return [_send(chat_id, NOT_OPEN, NO_KEYBOARD)]
        return [_send(chat_id, greeting(), KEYBOARD if kind == "ok" else ADMIN_KEYBOARD)]

    if kind == "admin":
        if text.lower() in ("/кто", "/who"):
            return [_send(chat_id, bot_contacts.who_now(db, now=now), ADMIN_KEYBOARD)]
        if text == BROADCAST_BUTTON:
            return _broadcast_start(db, chat_id, tid, now)
        typed = text and not text.startswith("/") and text != BUTTON_TEXT  # старая кнопка пароля — не текст
        pending = bot_broadcast.awaiting_text(db, tid, now=now) if typed else None
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
        confirm = {"inline_keyboard": [[{"text": "Да, новый пароль",
                                         "callback_data": f"{CONFIRM_PREFIX}{now_ts}"}]]}
        return [_send(chat_id, ASK_RESET, confirm)]

    return [_send(chat_id, ONLY_THIS, KEYBOARD if kind == "ok" else ADMIN_KEYBOARD if kind == "admin" else None)]
