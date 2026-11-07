"""Проба через бот от агента — за подопечного диктора (29.10, просьба агента).

Диктор шлёт пробу сам и только по приглашению (`bot_uploads`). Агент ведёт актёров и
грузит за них по роду занятий — на сайте он и так выбирает, за кого загрузка. В боте то
же: файл → «Чья это проба?» → роль → в воркер тем же путём, что проба диктора.

Подопечные (решение владельца 29.10) — все, кого предлагали в «Касте» (голоса) или
поставили на пробу («Имя?»), в неозвученных книгах. Своего списка у агента нет, и голосов
агента в базе тоже нет — поэтому не «кого предложила она», а «кого предлагали вообще».
Роль — любая, без приглашения: сначала роли, куда этого человека предлагали, затем
«Другая роль…» со всем кастом книги.
"""
from __future__ import annotations

from datetime import datetime

from app.models import BotUpload, Character, RoleVote, ScriptBook
from app.services import studio
from app.services.bot_out import Outgoing, send
from app.services.bot_uploads import CHOICE_TTL, _site_link
from app.services.role_deadlines import same_person
from app.services.telegram import max_download_bytes, size_text

BA_PREFIX = "ba:"
#: кнопок на странице: длинный список Telegram показывает простынёй, которую не пролистать
PAGE = 20
WHOSE = 'Whose audition is this?'


def _fold(value: str) -> str:
    return str(value or "").lower().replace("ё", "е").strip()


def _open_books(db) -> dict[str, ScriptBook]:
    return {book.id: book for book in db.query(ScriptBook).filter(ScriptBook.voiced_at.is_(None))}


def _proposals(db) -> list[tuple[str, Character]]:
    """(имя актёра, роль) — все предложения в неозвученных книгах: голоса и «Имя?» в касте."""
    from app.v2.cast_ops import parse_actor_name

    books = _open_books(db)
    characters = {ch.id: ch for ch in db.query(Character).all() if ch.book_id in books}
    found = []
    for ch in characters.values():
        name, tentative = parse_actor_name(str(ch.actor_name or ""))
        if name and tentative:
            found.append((name, ch))
    for vote in db.query(RoleVote).all():
        ch = characters.get(vote.character_id)
        name = str(vote.actor_name or "").strip()
        if ch is not None and name:
            found.append((name, ch))
    return found


def wards(db) -> list[str]:
    """Подопечные по алфавиту; одно лицо в двух написаниях — один раз (первое написание —
    из каста: по нему лист проб находит пару)."""
    names: list[str] = []
    for name, _ in _proposals(db):
        if not any(same_person(name, known) for known in names):
            names.append(name)
    return sorted(names, key=_fold)


def _cancel(upload_id: str) -> list[dict]:
    return [{"text": 'Cancel', "callback_data": f"{BA_PREFIX}x:{upload_id}"}]


def _pager(upload_id: str, page: int, total: int, step: str) -> list[dict]:
    nav = []
    if page > 0:
        nav.append({"text": '‹ Back', "callback_data": f"{BA_PREFIX}{step}:{upload_id}:{page - 1}"})
    if (page + 1) * PAGE < total:
        nav.append({"text": 'Next ›', "callback_data": f"{BA_PREFIX}{step}:{upload_id}:{page + 1}"})
    return nav


def _ward_page(db, chat_id, row: BotUpload, page: int) -> list[Outgoing]:
    names = wards(db)
    page = max(0, min(page, (len(names) - 1) // PAGE))
    start = page * PAGE
    rows = [[{"text": name[:60], "callback_data": f"{BA_PREFIX}w:{row.id}:{start + offset}"}]
            for offset, name in enumerate(names[start:start + PAGE])]
    nav = _pager(row.id, page, len(names), "wp")
    keyboard = [*rows, *([nav] if nav else []), _cancel(row.id)]
    return [send(chat_id, WHOSE, {"inline_keyboard": keyboard})]


def receive(db, *, tid: str, chat_id, info: dict, now: datetime) -> list[Outgoing]:
    if info.get("bad"):
        return [send(chat_id, 'Send the audition as an audio file or voice message.')]
    limit = max_download_bytes()
    if info["file_size"] > limit:
        return [send(chat_id, f'The file is larger than {size_text(limit)} — upload the audition on the website: {_site_link()}')]
    if not wards(db):
        return [send(chat_id, f'No actors are available. Actors proposed for roles in Cast appear here. Upload the audition on the website: {_site_link()}')]
    row = BotUpload(telegram_user_id=str(tid), created_at=now, updated_at=now, purpose="agent",
                    **{k: info[k] for k in ("file_id", "file_unique_id", "kind", "mime_type", "file_name",
                                            "file_size", "duration")})
    db.add(row)
    db.flush()
    return _ward_page(db, chat_id, row, 0)


def _role_label(ch: Character, books: dict[str, ScriptBook]) -> str:
    book = books.get(ch.book_id)
    return f"«{ch.name}» («{book.title if book else ''}»)"[:60]


def _proposed_roles(db, row: BotUpload) -> list[Outgoing]:
    books = _open_books(db)
    roles: list[Character] = []
    for name, ch in _proposals(db):
        if same_person(name, row.actor_name) and ch not in roles:
            roles.append(ch)
    roles.sort(key=lambda ch: (books[ch.book_id].title if ch.book_id in books else "", _fold(ch.name)))
    keyboard = [[{"text": _role_label(ch, books), "callback_data": f"{BA_PREFIX}r:{row.id}:{ch.id}"}] for ch in roles]
    keyboard.append([{"text": 'Another role…', "callback_data": f"{BA_PREFIX}o:{row.id}"}])
    keyboard.append(_cancel(row.id))
    return [send(row.telegram_user_id, f'Audition {row.actor_name} — which role?', {"inline_keyboard": keyboard})]


def _cast(db, book_id: str) -> list[Character]:
    """Роли книги по алфавиту. Роль без единой реплики по разметке записывать нечем — её нет."""
    from app.v2.cast_ops import role_chapter_map

    chapters = role_chapter_map(db, book_id)
    cast = db.query(Character).filter(Character.book_id == book_id).all()
    if chapters is not None:
        cast = [ch for ch in cast if chapters.get(str(ch.id))]
    return sorted(cast, key=lambda ch: _fold(ch.name))


def _books_step(db, row: BotUpload) -> list[Outgoing]:
    books = sorted(_open_books(db).values(), key=lambda book: _fold(book.title))
    if len(books) == 1:
        return _cast_page(db, row, books[0].id, 0)
    keyboard = [[{"text": str(book.title or "")[:60], "callback_data": f"{BA_PREFIX}b:{row.id}:{book.id}"}]
                for book in books]
    return [send(row.telegram_user_id, 'Which book is the role from?', {"inline_keyboard": [*keyboard, _cancel(row.id)]})]


def _cast_page(db, row: BotUpload, book_id: str, page: int) -> list[Outgoing]:
    cast = _cast(db, book_id)
    if not cast:
        return [send(row.telegram_user_id, 'This book has no roles yet.')]
    page = max(0, min(page, (len(cast) - 1) // PAGE))
    start = page * PAGE
    keyboard = [[{"text": str(ch.name or "")[:60], "callback_data": f"{BA_PREFIX}r:{row.id}:{ch.id}"}]
                for ch in cast[start:start + PAGE]]
    nav = _pager(f"{row.id}:{book_id}", page, len(cast), "rp")
    return [send(row.telegram_user_id, f'Audition {row.actor_name} — which role?',
                 {"inline_keyboard": [*keyboard, *([nav] if nav else []), _cancel(row.id)]})]


def press(db, *, tid: str, chat_id, data: str, now: datetime) -> list[Outgoing]:
    parts = data[len(BA_PREFIX):].split(":")
    action, upload_id, rest = parts[0], (parts[1] if len(parts) > 1 else ""), parts[2:]
    row = db.get(BotUpload, upload_id)
    if row is None or row.telegram_user_id != str(tid) or row.purpose != "agent":
        return []  # чужое нажатие молча гасим
    if action == "x":
        if row.state == "choose":
            row.state, row.updated_at = "cancelled", now
            db.flush()
        if row.state == "cancelled":
            return [send(chat_id, 'Cancelled.')]
        return [send(chat_id, f'The audition has already been accepted and cannot be cancelled in the bot. If needed, {studio.write_to()}.')]
    if row.state != "choose":
        return [send(chat_id, 'This audition has already been accepted or cancelled.')]
    if row.created_at < now - CHOICE_TTL:
        return [send(chat_id, 'This selection has expired. Send the file again.')]

    if action == "wp":
        return _ward_page(db, chat_id, row, int(rest[0]) if rest and rest[0].isdigit() else 0)
    if action == "w":
        names = wards(db)
        index = int(rest[0]) if rest and rest[0].isdigit() else -1
        if not 0 <= index < len(names):
            return [send(chat_id, 'The list has changed. Send the file again.')]
        row.actor_name, row.updated_at = names[index], now
        db.flush()
        return _proposed_roles(db, row)
    if not row.actor_name:
        return [send(chat_id, 'First select whose audition this is.')]
    if action == "o":
        return _books_step(db, row)
    if action == "b":
        return _cast_page(db, row, rest[0] if rest else "", 0)
    if action == "rp":
        book_id = rest[0] if rest else ""
        return _cast_page(db, row, book_id, int(rest[1]) if len(rest) > 1 and rest[1].isdigit() else 0)
    if action != "r":
        return []
    ch = db.get(Character, rest[0] if rest else "")
    if ch is None or ch.book_id not in _open_books(db):
        return [send(chat_id, 'This role no longer exists. Send the file again.')]
    # Захват одной записью, как у проб диктора: два нажатия подряд — одна задача.
    claimed = (db.query(BotUpload)
               .filter(BotUpload.id == row.id, BotUpload.state == "choose")
               .update({"state": "queued", "character_id": ch.id, "updated_at": now}, synchronize_session=False))
    if claimed != 1:
        return []
    db.expire(row)
    return [send(chat_id, f'Saving audition «{ch.name}», {row.actor_name} — confirmation will arrive when it is stored, usually within a few minutes.'),
            Outgoing("enqueue_bot_upload", {"id": row.id})]
