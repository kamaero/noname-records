"""Благодарственное письмо дикторам озвученной книги (план book-lifecycle, задача 4).

Письмо уходит черновиком с кнопкой «Отправить»: владелец правит текст и сам нажимает.
"""
from __future__ import annotations

from app.config import settings
from app.models import AudioFile, BookThanksSent, ScriptBook
from app.services import role_deadlines
from app.services.audio_naming import book_token
from app.services.audio_uploads import derive_book_code
from app.time_utils import iso_utc, utcnow_naive

#: Тон — владельца: тепло, без восклицательных знаков россыпью, с клиффхэнгером в конце.
DEFAULT_TEXT = (
    "{name}, thank you for bringing «{book}» to life. "
    "Your work on {roles} helped shape this audiobook, and it was a pleasure working with you.\n\n"
    "We hope to work with you again on future projects."
)


def first_name(actor: str) -> str:
    """Имя в обращении. Общего помощника у бота нет: актёр записан «Фамилия Имя», берём второе слово."""
    words = str(actor or "").split()
    return words[1] if len(words) > 1 else (words[0] if words else "")


def _join_roles(roles: list[str]) -> str:
    if len(roles) <= 1:
        return "".join(roles)
    return ", ".join(roles[:-1]) + " and " + roles[-1]


def render(text: str, *, name: str, roles: list[str], book: str = "") -> str:
    # Не str.format: в правленом владельцем тексте могут оказаться посторонние фигурные скобки.
    return (text.replace("{name}", name).replace("{roles}", _join_roles(roles)).replace("{book}", book))


def recipients(db, book_id: str) -> dict:
    book = db.get(ScriptBook, book_id)
    if book is None:
        raise LookupError(book_id)
    wanted = book_token(derive_book_code(str(book.title or "")))
    by_actor: dict[str, list[str]] = {}
    for audio in db.query(AudioFile).filter(AudioFile.kind == "take").order_by(AudioFile.id):
        if book_token(str(audio.book_code or "")) != wanted:
            continue
        actor = str(audio.actor_name or "").strip()
        role = str(audio.role or "").strip()
        if not actor:
            continue
        roles = by_actor.setdefault(actor, [])
        if role and role not in roles:
            roles.append(role)

    owner = str(settings.owner_telegram_id or "").strip()
    sent = {row.telegram_user_id: row for row in
            db.query(BookThanksSent).filter(BookThanksSent.book_id == book_id)}
    merged: dict[str, dict] = {}
    no_chat: list[dict] = []
    for actor, roles in by_actor.items():
        chat = role_deadlines.chat_for(db, actor)
        if not chat:
            no_chat.append({"actor": actor, "roles": roles})
            continue
        if chat == owner:
            continue
        mark = sent.get(chat)
        person = merged.setdefault(chat, {
            "actor": actor, "chat": chat, "roles": [], "name": first_name(actor),
            "sent_at": iso_utc(mark.sent_at) or "" if mark else "",
            # sent_at без delivered — отправка упала: владельцу писать самому
            "delivered": bool(mark and mark.delivered)})
        person["roles"].extend(r for r in roles if r not in person["roles"])
    return {"people": list(merged.values()), "no_chat": no_chat}


def _default_send(db):
    from app.services.telegram import send_telegram_message

    # direct=True: иначе OWNER_TELEGRAM_ID подменит адресата и письмо уйдёт владельцу.
    return lambda chat, text: bool(send_telegram_message(db, text, chat_ids=[chat], direct=True))


def send(db, book_id: str, text: str, *, send_message=None, now=None) -> dict:
    book = db.get(ScriptBook, book_id)
    if book is None:
        raise LookupError(book_id)
    send_message = send_message or _default_send(db)
    now = now or utcnow_naive()
    out = {"sent": [], "failed": [], "already": []}
    for person in recipients(db, book_id)["people"]:
        if person["sent_at"]:
            out["already"].append(person["actor"])
            continue
        # Отметка — до отправки и с коммитом (как в role_deadlines.run_pass): упавшая
        # отправка не приведёт к повторному письму тому же человеку.
        mark = BookThanksSent(book_id=book_id, telegram_user_id=person["chat"],
                              actor_name=person["actor"], sent_at=now, delivered=False)
        db.add(mark)
        db.commit()
        body = render(text, name=person["name"], roles=person["roles"],
                      book=str(book.display_title or book.title or ""))
        ok = False
        try:
            ok = bool(send_message(person["chat"], body))
        except Exception:  # noqa: BLE001 — один адресат не должен сорвать остальных
            ok = False
        if ok:
            mark.delivered = True
            db.commit()
        out["sent" if ok else "failed"].append(person["actor"])
    return out
