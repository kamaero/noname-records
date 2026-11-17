"""Подробности книги в студии: страницы вместо обрезанного списка ролей."""
from __future__ import annotations

import re

from app.models import ScriptBook
from app.services.bot_status import _esc
from app.v2.cast_ops import parse_actor_name

GROUPS = {
    "u": "No approved actor",
    "z": "No accepted takes",
    "p": "Partially recorded",
}


def progress_rows(db, book_id):
    from app.models import AudioFile, Character, ScriptChapter
    from app.services.chapter_delivery import _chapter_label
    from app.services.role_deadlines import same_person, wanted_chapters
    from app.services.role_identity import RoleIdentityError, book_roles
    from app.v2.cast_ops import is_placeholder_name

    identities = book_roles(db, book_id)
    chapters = db.query(ScriptChapter).filter_by(book_id=book_id).all()
    takes = db.query(AudioFile).filter_by(kind="take").all()
    rows = []
    for character in db.query(Character).filter_by(book_id=book_id).all():
        if is_placeholder_name(character.name):
            continue
        wanted = wanted_chapters(db, character)
        required = [chapter for chapter in chapters if chapter.chapter_index in wanted]
        if not required:
            continue
        actor, tentative = parse_actor_name(character.actor_name)
        labels = set()
        for audio in takes:
            try:
                matches = identities.matches_character_audio(audio, character)
            except RoleIdentityError:
                matches = False
            if matches and actor and not tentative and same_person(audio.actor_name, actor):
                labels.add(str(audio.chapter or "").strip())
        missing = sorted(chapter.chapter_index for chapter in required if _chapter_label(chapter) not in labels)
        rows.append({"character_id": character.id, "name": character.name,
                     "actor_name": character.actor_name or "", "recorded_chapters": len(required) - len(missing),
                     "total_chapters": len(required), "missing_chapters": missing,
                     "complete": bool(actor and not tentative and not missing)})
    return rows


def groups(rows):
    result = {key: [] for key in GROUPS}
    for row in rows:
        actor, tentative = parse_actor_name(row["actor_name"])
        key = "u" if not actor or tentative else (
            "z" if not row["recorded_chapters"] else "p" if not row["complete"] else ""
        )
        if key:
            result[key].append(row)
    for values in result.values():
        values.sort(key=lambda row: (row["name"].casefold().replace("ё", "е"), row["character_id"]))
    return result


def chapter_ranges(indices):
    numbers = sorted(set(indices))
    spans = []
    for number in numbers:
        if spans and number == spans[-1][1] + 1:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ", ".join(str(start) if start == end else f"{start}–{end}" for start, end in spans)


def _short(value, limit):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _button(text, key):
    return {"text": text, "callback_data": "ss:" + key}


def _size(text):
    # Telegram ограничивает сообщение в кодовых единицах UTF-16, а не len(str).
    return len(text.encode("utf-16-le")) // 2


def _pages(items):
    pages, current = [], []
    for item in items:
        if current and (_size("\n\n".join([*current, item])) > 2500 or len(current) >= 10):
            pages.append(current)
            current = []
        current.append(item)
    return [*pages, current] if current else pages or [[]]


def _role_lines(row):
    actor, tentative = parse_actor_name(row["actor_name"])
    who = ("candidate: " if tentative else "") + _short(actor, 100) if actor else "no actor assigned"
    base = (f"• <b>{_esc(_short(row['name'], 160))}</b> — {_esc(who)}\n"
            f"  Chapters with a take: {row['recorded_chapters']}/{row['total_chapters']}")
    missing = chapter_ranges(row["missing_chapters"])
    if not missing:
        return [base]
    # Даже очень длинный перечень глав сохраняется целиком, с повтором имени роли.
    chunks, current = [], ""
    for part in missing.split(", "):
        if current and len(current) + len(part) > 800:
            chunks.append(current)
            current = ""
        current += (", " if current else "") + part
    chunks.append(current)
    return [base + "\n  Missing takes in chapters: " + chunk for chunk in chunks]


def reply(db, key):
    """Вызывается только после проверки служебных прав в bot_dialog."""
    parts = key.split(":")
    if key == "b" or (len(parts) == 2 and parts[0] == "books" and re.fullmatch(r"\d{1,6}", parts[1])):
        books = db.query(ScriptBook).filter(ScriptBook.voiced_at.is_(None)).order_by(
            ScriptBook.title, ScriptBook.id).all()
        pages = [books[n:n + 8] for n in range(0, len(books), 8)] or [[]]
        page = min(int(parts[1]) if len(parts) == 2 else 0, len(pages) - 1)
        buttons = [[_button(_short(book.title, 64), f"book:{book.id}")] for book in pages[page]]
        nav = []
        if page:
            nav.append(_button("‹ Previous", f"books:{page - 1}"))
        if page + 1 < len(pages):
            nav.append(_button("Next ›", f"books:{page + 1}"))
        if nav:
            buttons.append(nav)
        return ("<b>📚 Books</b>\nChoose a book for role and chapter details."
                if books else "<b>📚 Books</b>\nNo active books."), {"inline_keyboard": buttons}
    if not (len(parts) == 2 and parts[0] == "book" or len(parts) == 4 and parts[0] == "roles"
            and parts[2] in GROUPS and re.fullmatch(r"\d{1,6}", parts[3])):
        return "", None
    book_id = parts[1]
    if not re.fullmatch(r"[\w-]{1,36}", book_id, flags=re.ASCII):
        return "", None
    book = db.get(ScriptBook, book_id)
    if book is None or book.voiced_at is not None:
        return "This book is no longer active.", {"inline_keyboard": [[_button("‹ Books", "b")]]}
    rows = progress_rows(db, book.id)
    selected = groups(rows)
    title = f"<b>📚 {_esc(_short(book.title, 160))}</b>"
    buttons = [[_button(f"{name}: {len(selected[group])}", f"roles:{book.id}:{group}:0")]
               for group, name in GROUPS.items()]
    if parts[0] == "book":
        text = "\n".join([title, "", f"Fully recorded: {sum(bool(row['complete']) for row in rows)}/{len(rows)}",
                          *[f"{name}: {len(selected[group])}" for group, name in GROUPS.items()], "",
                          "Completion counts chapters with accepted takes. Auditions are excluded.",
                          "Tentative assignments marked '?' have no approved actor."])
        buttons.append([_button("‹ Books", "b")])
        return text, {"inline_keyboard": buttons}
    group = parts[2]
    pages = _pages([line for row in selected[group] for line in _role_lines(row)])
    page = min(int(parts[3]), len(pages) - 1)
    text = "\n\n".join([title, f"<b>{GROUPS[group]}: {len(selected[group])}</b>",
                          f"Page {page + 1}/{len(pages)}",
                          *pages[page]]) + ("" if selected[group] else "\nNone.")
    nav = []
    if page:
        nav.append(_button("‹ Previous", f"roles:{book.id}:{group}:{page - 1}"))
    if page + 1 < len(pages):
        nav.append(_button("Next ›", f"roles:{book.id}:{group}:{page + 1}"))
    return text, {"inline_keyboard": ([nav] if nav else []) + [[_button("‹ Book", f"book:{book.id}")]]}

