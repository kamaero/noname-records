"""Ответы бота и кнопки — общее для диалога (`bot_dialog`), кнопок сроков (`bot_deadlines`) и
писем (`role_approval`, `role_deadlines`). Отдельным модулем, чтобы письма и диалог не
импортировали друг друга по кругу.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DL_PREFIX = "dl:"   # кнопки диктора под письмом: Беру, Не смогу, время, вариант продления
DD_PREFIX = "dd:"   # решение владельца после «Не смогу»: снять с роли, оставить
DX_PREFIX = "dx:"   # решение владельца по запросу продления


@dataclass(frozen=True)
class Outgoing:
    method: str
    payload: dict = field(default_factory=dict)


def send(chat_id, text: str, markup: dict | None = None, *, html_mode: bool = False) -> Outgoing:
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if markup is not None:
        payload["reply_markup"] = markup
    if html_mode:
        payload["parse_mode"] = "HTML"
    return Outgoing("sendMessage", payload)


def edit_off(chat_id, message_id) -> Outgoing:
    """Погасить кнопки решённого сообщения: второе нажатие не должно голосовать снова."""
    return Outgoing("editMessageReplyMarkup",
                    {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})


def time_markup(character_id: str) -> dict:
    return {"inline_keyboard": [[{"text": '⏳ Need more time', "callback_data": f"{DL_PREFIX}t:{character_id}"}]]}


def invite_markup(character_id: str) -> dict:
    return {"inline_keyboard": [
        [{"text": '✅ Accept', "callback_data": f"{DL_PREFIX}y:{character_id}"},
         {"text": '✖️ Decline', "callback_data": f"{DL_PREFIX}n:{character_id}"}],
        *time_markup(character_id)["inline_keyboard"],
    ]}
