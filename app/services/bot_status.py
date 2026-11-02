"""Команда боту `/status` — что у диктора сейчас: роли, пробы, сроки, отказы, вход на сайт.

Повод (05.10): дикторы переспрашивали владельца «на что я утверждён и до какого числа» —
ответ разбросан по письмам бота, а письма теряются в ленте. Сводка собирается заново на
каждый запрос из тех же таблиц, по которым уходят письма, поэтому не может разойтись с ними.

Чья строка каста — решает `chat_for`, тот же разбор имени, что у писем о назначении и
напоминаний: имя в касте должно указывать ровно на этого человека. Двусмысленное имя
(две «Ольги») не показывается никому — иначе диктор увидел бы чужую роль, а письма по ней
всё равно не получает. Отказ показывается только отправленный: ждущий ещё может
отмениться переигранным утверждением, и сводка не должна сообщать то, чего письмо не скажет.
"""
from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.services import studio
from app.models import AudioFile, AuditionRejection, Character, RoleDeadline, ScriptBook, TelegramAuthAccount, User
from app.services.audio_uploads import AUDITION, derive_book_code
from app.services.audition_rejections import SENT, character_for_role
from app.services.author_profile import normalize_name
from app.services.login_identity import TELEGRAM_PASSWORD_HASH
from app.services.role_deadlines import chat_for, format_due, role_progress, same_person
from app.services.telegram import names_match
from app.v2.cast_ops import parse_actor_name

LOGIN_URL = studio.site_url("/app/login")
#: Telegram режет сообщение на 4096 знаках; запас — на хвост «и ещё…»
TEXT_LIMIT = 3800


def _esc(text: str) -> str:
    """Telegram-HTML требует экранировать только <, > и &; кавычки как `&#x27;` он
    показывает буквально — «Ге&#x27;Коон» вместо «Ге'Коон»."""
    return html.escape(str(text or ""), quote=False)


@dataclass
class _Mine:
    """«Это имя в касте — этот человек». Окончательно решает `chat_for`, но он перебирает
    все учётки и стоил 3,4 с на сводку (120 имён каста × 113 учёток на проде 05.10). Имя
    может указать на человека, только если подходит к одному из его собственных имён —
    то же условие, что в `_matching_accounts`, — поэтому сначала дешёвая сверка с ними."""
    tid: str
    own_names: list[str]
    cache: dict = field(default_factory=dict)

    def __call__(self, db, actor: str) -> bool:
        name = str(actor or "").strip()
        if not name:
            return False
        if name not in self.cache:
            self.cache[name] = (any(names_match(name, own) for own in self.own_names)
                                and chat_for(db, name) == self.tid)
        return self.cache[name]


def _own_names(db, tid: str, user: User | None) -> list[str]:
    names = [str(user.display_name or "") if user is not None else ""]
    names += [str(row.display_name or "") for row in db.query(TelegramAuthAccount)
              .filter(TelegramAuthAccount.telegram_user_id == str(tid), TelegramAuthAccount.is_active == "true")]
    return [name for name in dict.fromkeys(names) if name.strip()]


@dataclass
class PersonLines:
    """Готовые строки разделов о человеке — общие для `/status` и карточки `/dictor`."""
    roles: list[str]
    auditions: list[str]
    rejected: list[str]


def owner_by_chat(db, tid: str, user: User | None) -> Callable[[str], bool]:
    """Чьи строки каста — для человека с Telegram: как у писем (`chat_for`)."""
    mine = _Mine(tid=str(tid), own_names=_own_names(db, tid, user))
    return lambda actor: mine(db, actor)


def owner_by_name(name: str) -> Callable[[str], bool]:
    """Для актёра без Telegram (есть только в касте): письма ему не уходят, и `chat_for`
    его не найдёт — сверяем имя напрямую, тем же `same_person`, что и сроки."""
    cache: dict[str, bool] = {}

    def owns(actor: str) -> bool:
        key = str(actor or "").strip()
        if key not in cache:
            cache[key] = same_person(key, name)
        return cache[key]
    return owns


def _account_lines(user: User | None, *, admin: bool) -> list[str]:
    if user is None:
        return []
    lines = [f"Учётка: {_esc(user.display_name or user.login)} (логин <code>{_esc(user.login)}</code>)"]
    if not admin:
        if str(user.password_hash or "") == TELEGRAM_PASSWORD_HASH:
            lines.append("Пароля нет — вход через Telegram или кнопка «🔑 Логин и пароль».")
        else:
            lines.append("Пароль выдан. Забыли — кнопка «🔑 Логин и пароль».")
    lines.append(f"Вход: {LOGIN_URL}")
    return lines


def _chapters(n: int) -> str:
    mod10, mod100 = n % 10, n % 100
    if mod10 == 1 and mod100 != 11:
        return "главы"
    return "глав"


def person_lines(db, owns: Callable[[str], bool], *, now: datetime, you: bool = True) -> PersonLines:
    """Роли, пробы и отказы человека. `you=False` — карточка глазами админа: «ждём пробу»."""
    wait = f"ждём {'вашу ' if you else ''}пробу"
    # Озвученная книга в сводке диктора не показывается: ролей в ней больше нет.
    books = {b.id: b for b in db.query(ScriptBook).filter(ScriptBook.voiced_at.is_(None)).all()}
    by_code = {derive_book_code(str(b.title or "")): b for b in books.values()}

    def title(book_id: str) -> str:
        return _esc(str(getattr(books.get(book_id), "title", "") or ""))

    open_deadlines: dict[str, RoleDeadline] = {
        row.character_id: row
        for row in db.query(RoleDeadline).filter(RoleDeadline.closed_at.is_(None)).all()
    }
    roles: list[str] = []
    invited: dict[tuple[str, str], Character] = {}
    approved_keys: set[tuple[str, str]] = set()
    #: роли, на которые кто-то уже утверждён, — проба на такую больше «решения» не ждёт
    taken: set[tuple[str, str]] = set()
    for ch in db.query(Character).order_by(Character.book_id, Character.name).all():
        if ch.book_id not in books:
            continue
        name, tentative = parse_actor_name(str(ch.actor_name or ""))
        key = (ch.book_id, normalize_name(str(ch.name or "")))
        if name and not tentative:
            taken.add(key)
        if not owns(name):
            continue
        if tentative:
            invited[key] = ch
            continue
        approved_keys.add(key)
        done, total = role_progress(db, ch, name)
        line = f"• «{_esc(str(ch.name or ''))}» — «{title(ch.book_id)}»"
        if total:
            line += f": сдано {done} из {total} {_chapters(total)}"
        deadline = open_deadlines.get(ch.id)
        if total and done >= total:
            line += " ✓"
        elif deadline is not None and deadline.kind == "role":
            late = " — срок вышел" if deadline.due_at < now else ""
            line += f", срок до {format_due(deadline.due_at, kind='role')}{late}"
        roles.append(line)

    rejected: list[str] = []
    rejected_keys: set[tuple[str, str]] = set()
    for rej in db.query(AuditionRejection).filter(AuditionRejection.status == SENT).all():
        if rej.book_id in books and owns(rej.actor_name):
            rejected_keys.add((rej.book_id, normalize_name(rej.role)))
            rejected.append(f"• «{_esc(rej.role)}» — «{title(rej.book_id)}»: выбран другой исполнитель")

    rejected.sort(key=lambda line: line.lower())

    # Пробы: позвали (имя в касте со «?») и/или проба уже загружена. Роль, на которую
    # человек утверждён или по которой ему отказали, здесь не дублируется. Роль, отданная
    # другому, без отправленного отказа не показывается вовсе: «ждёт решения» было бы
    # неправдой, а «выбран другой» раньше письма сказало бы то, что письмо ещё может не
    # сказать (отказ ждёт 15 минут и отменяется переигранным утверждением).
    uploaded: set[tuple[str, str]] = set()
    # ключ пары нормализован (регистр, ударения); показывать — написание из каста
    shown_name: dict[tuple[str, str], str] = {key: str(ch.name or "") for key, ch in invited.items()}
    characters_by_book: dict[str, list[Character]] = {}
    for item in db.query(AudioFile).filter(AudioFile.kind == AUDITION).all():
        book = by_code.get(str(item.book_code or ""))
        if book is None or not owns(item.actor_name):
            continue
        cast = characters_by_book.setdefault(book.id, db.query(Character).filter_by(book_id=book.id).all())
        ch = character_for_role(cast, str(item.role or ""))
        name = str(ch.name if ch is not None else item.role or "")
        key = (book.id, normalize_name(name))
        uploaded.add(key)
        shown_name.setdefault(key, name)
    auditions: list[str] = []
    for key in sorted(set(invited) | uploaded):
        if key in approved_keys or key in rejected_keys or key in taken:
            continue
        ch = invited.get(key)
        line = f"• «{_esc(shown_name[key])}» — «{title(key[0])}»: "
        if key in uploaded:
            line += "проба загружена, ждёт решения"
        else:
            deadline = open_deadlines.get(ch.id)
            if deadline is not None and deadline.kind == "audition" and deadline.accepted_at is not None:
                # «✅ Беру» в боте (пакет B): человек ответил — не «ждём», а «взята»
                line += "проба взята — срок вышел" if deadline.due_at < now else \
                    f"проба взята, ждём до {format_due(deadline.due_at, kind='time')}"
            elif deadline is not None and deadline.kind == "audition":
                line += f"{wait} — срок вышел" if deadline.due_at < now else \
                    f"{wait} до {format_due(deadline.due_at, kind='time')}"
            else:
                line += wait
        auditions.append(line)

    return PersonLines(roles=roles, auditions=auditions, rejected=rejected)


def status_text(db, tid: str, user: User | None, *, admin: bool, now: datetime) -> str:
    """Сводка для `/status`. HTML-разметка Telegram (`parse_mode=HTML`)."""
    lines = person_lines(db, owner_by_chat(db, tid, user), now=now)
    parts = [f"📋 <b>Ваш статус в {html.escape(studio.name())}</b>", ""]
    parts += _account_lines(user, admin=admin)
    parts += ["", "🎬 <b>Утверждённые роли</b>"] + (lines.roles or ["Пока нет."])
    parts += ["", "🎙 <b>Пробы</b>"] + (lines.auditions or ["Приглашений на пробу сейчас нет."])
    if lines.rejected:
        parts += ["", "✉️ <b>Отказы</b>"] + lines.rejected
    parts += ["", f"Записи — на сайте, раздел «Загрузка». По книге, которая сейчас в работе студии, дубль можно "
              f"прислать и файлом в бот. Вопросы и продление срока — {studio.write_to()}."]
    return _fit(parts)


def _fit(parts: list[str]) -> str:
    """Длинный список ролей не должен ронять ответ: Telegram отклонит его целиком."""
    text = "\n".join(parts)
    if len(text) <= TEXT_LIMIT:
        return text
    kept: list[str] = []
    for part in parts:
        if len("\n".join(kept + [part])) > TEXT_LIMIT:
            break
        kept.append(part)
    return "\n".join(kept + ["", f"…список длинный, полностью — на сайте: {LOGIN_URL}"])
