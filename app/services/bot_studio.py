"""Обзор студии в боте: `/studiostatus` и `/dictor` (пакет A, спека 2026-10-05-bot-studio-overview).

Админ, агент и автор собирали картину студии по сайту и утреннему дайджесту сроков. Здесь —
четыре раздела (пробы ждут решения, сроки, книги, кто молчит) и карточка диктора.

Всё считается заново на каждый запрос, но за один проход: дубли, пробы, главы и сроки
грузятся по разу. `role_progress` ходит в базу на каждого персонажа — на 612 персонажах
прода это секунды, а обработчик вебхука один. `Studio.progress` обязан совпадать с ним
(сторож — `test_progress_matches_role_progress`).

Денег здесь нет: агент на сайте гонораров не видит, бот не открывает больше сайта.
"""
from __future__ import annotations

from app.services.studio_calendar import studio_timezone

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.models import (
    AudioFile, AuditionRejection, BotContact, Character, RoleDeadline, ScriptBook, ScriptChapter, TelegramAuthAccount,
    User,
)
from app.services.audio_uploads import AUDITION, TAKE, derive_book_code
from app.services.audition_rejections import SENT, UNKNOWN_ACTOR, role_keys
from app.services.author_profile import normalize_name
from app.services.chapter_delivery import _chapter_label
from app.services.bot_status import _esc, _fit, owner_by_chat, owner_by_name, person_lines
from app.services.role_deadlines import chat_for, format_due, same_person, wanted_chapters
from app.services.telegram import _normalize_person_name, names_match
from app.v2.cast_ops import parse_actor_name

#: «скоро» в разделе сроков (спека): трое суток
SOON = timedelta(days=3)


@dataclass
class Pending:
    book_title: str
    role: str
    actor: str
    first_at: datetime


@dataclass
class Due:
    actor: str
    kind: str          # audition | role
    role: str
    book_title: str
    due_at: datetime


@dataclass
class BookProgress:
    """Роли книги, у которых есть главы: записано целиком, утверждено, свободно, всего."""
    title: str
    recorded: int
    approved: int
    free: int
    roles: int


@dataclass
class Silent:
    actor: str
    chat: str
    last_seen: datetime | None
    items: list[str] = field(default_factory=list)


class Studio:
    """Снимок студии на момент запроса: всё нужное разделам загружено по разу."""

    def __init__(self, db, *, now: datetime):
        self.db, self.now = db, now
        # Озвученные книги из обзора студии исключены целиком: роли, сроки и пробы по ним
        # дальше не считаются (каст, characters и open_deadlines строятся от этого словаря).
        self.book_by_id = {b.id: b for b in db.query(ScriptBook).filter(ScriptBook.voiced_at.is_(None)).all()}
        self.code_of = {bid: derive_book_code(str(b.title or "")) for bid, b in self.book_by_id.items()}
        self.book_by_code = {code: self.book_by_id[bid] for bid, code in self.code_of.items()}
        self.characters = [ch for ch in db.query(Character).order_by(Character.book_id, Character.name).all()
                           if ch.book_id in self.book_by_id]
        self.character_by_id = {ch.id: ch for ch in self.characters}
        self.cast_by_book: dict[str, list[Character]] = {}
        for ch in self.characters:
            self.cast_by_book.setdefault(ch.book_id, []).append(ch)
        self.labels: dict[str, dict[int, str]] = {}
        for row in db.query(ScriptChapter).all():
            self.labels.setdefault(row.book_id, {})[int(row.chapter_index)] = _chapter_label(row)
        self.takes: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self.auditions: list[AudioFile] = []
        for item in db.query(AudioFile).filter(AudioFile.kind.in_((TAKE, AUDITION))).all():
            if item.kind == TAKE:
                self.takes.setdefault((str(item.book_code or ""), str(item.role or "")), []).append(
                    (str(item.chapter or "").strip(), str(item.actor_name or "")))
            else:
                self.auditions.append(item)
        self.open_deadlines = [row for row in (db.query(RoleDeadline).filter(RoleDeadline.closed_at.is_(None))
                                               .order_by(RoleDeadline.due_at.asc()).all())
                               if row.book_id in self.book_by_id]
        # Роль пробы → персонаж: `character_for_role` пересобирает ключи всего каста на
        # каждый вызов — 2057 вызовов «Молчат» стоили 8,9 с на проде 05.10. Индекс — один раз.
        self.role_index: dict[str, dict[str, Character]] = {}
        for book_id, cast in self.cast_by_book.items():
            index: dict[str, Character] = {}
            for ch in cast:
                for key in role_keys(ch.name, ch.aliases):
                    index.setdefault(key, ch)
            self.role_index[book_id] = index
        #: (книга, ключ роли, актёр, когда загружена) — каждая проба, сопоставленная с ролью
        self.audition_pairs: list[tuple[str, str, str, str, datetime]] = []
        for item in self.auditions:
            book = self.book_by_code.get(str(item.book_code or ""))
            if book is None:
                continue
            ch = self.role_index[book.id].get(normalize_name(str(item.role or ""))) if book.id in self.role_index else None
            role = str(ch.name if ch is not None else item.role or "")
            self.audition_pairs.append((book.id, normalize_name(role), role, str(item.actor_name or "").strip(),
                                        item.uploaded_at))
        #: коды книг, где есть хотя бы один дубль: «роль не начата» — молчание только там
        #: (владелец 05.10: иначе в «Молчат» все утверждённые на ещё не начатые книги)
        self.recording_started = {code for code, _ in self.takes}
        self._accounts = self._account_index()
        # `names_match` говорит «да» только при общем слове (после той же нормализации) —
        # без этого фильтра 94 молчащих × 190 имён учёток стоили секунду на проде 05.10
        self._account_words = [(tid, names, [self._words(n) for n in names]) for tid, names in self._accounts]

    def _account_index(self) -> list[tuple[str, list[str]]]:
        """Включённые учётки и их имена (своё и связанного пользователя) — как в
        `role_approval._matching_accounts`, но одним запросом на весь снимок."""
        accounts = self.db.query(TelegramAuthAccount).filter(TelegramAuthAccount.is_active == "true").all()
        user_ids = [str(a.user_id or "") for a in accounts if str(a.user_id or "").strip()]
        linked = {str(u.id): str(u.display_name or "") for u in self.db.query(User).filter(User.id.in_(user_ids))} \
            if user_ids else {}
        return [(str(a.telegram_user_id or "").strip(), [str(a.display_name or ""), linked.get(str(a.user_id or ""), "")])
                for a in accounts]

    @staticmethod
    def _words(name: str) -> frozenset[str]:
        return frozenset(_normalize_person_name(name).split())

    def chat(self, actor: str) -> str:
        """Чат актёра, если имя указывает ровно на одну учётку, — решение `chat_for`
        (сторож — `test_studio_chat_index_agrees_with_chat_for`)."""
        wanted = str(actor or "").strip()
        if not wanted:
            return ""
        words = self._words(wanted)
        found = {tid for tid, names, name_words in self._account_words
                 if tid and any(n and words & w and names_match(wanted, n) for n, w in zip(names, name_words))}
        return next(iter(found)) if len(found) == 1 else ""

    def title(self, book_id: str) -> str:
        return str(getattr(self.book_by_id.get(book_id), "title", "") or "")

    def _character_of(self, ch):
        """Строка каста для роли: «Смета» передаёт не `Character`, а имя роли с книгой,
        а главы по разметке ищутся по id персонажа."""
        if getattr(ch, "id", None):
            return ch
        wanted = normalize_name(str(ch.name or ""))
        found = next((row for row in self.cast_by_book.get(ch.book_id, [])
                      if normalize_name(str(row.name or "")) == wanted), None)
        return found if found is not None else ch

    def progress(self, ch: Character, actor: str) -> tuple[int, int]:
        """(глав с дублем этого актёра, всего глав роли) — как `role_progress`, без запросов."""
        labels = self.labels.get(ch.book_id, {})
        chapters = {n: labels[n] for n in wanted_chapters(self.db, self._character_of(ch)) if n in labels}
        if not chapters:
            return 0, 0
        recorded = {chapter for chapter, who in self.takes.get((self.code_of[ch.book_id], str(ch.name or "")), [])
                    if same_person(who, actor)}
        return sum(1 for label in chapters.values() if label in recorded), len(chapters)

    def _taken(self) -> set[tuple[str, str]]:
        """Роли, на которые кто-то утверждён (имя без «?»)."""
        taken = set()
        for ch in self.characters:
            name, tentative = parse_actor_name(str(ch.actor_name or ""))
            if name and not tentative:
                taken.add((ch.book_id, normalize_name(str(ch.name or ""))))
        return taken

    def pending(self) -> list[Pending]:
        """Пары «книга + роль + актёр» с пробой, где роль свободна и отказ не отправлен —
        то же, что «проба загружена, ждёт решения» в `/status`."""
        taken = self._taken()
        sent = [(r.book_id, normalize_name(r.role), r.actor_name)
                for r in self.db.query(AuditionRejection).filter(AuditionRejection.status == SENT).all()]
        pairs: dict[tuple[str, str, str], Pending] = {}
        for book_id, key, role, actor, uploaded_at in self.audition_pairs:
            if (book_id, key) in taken or any(b == book_id and k == key and same_person(a, actor) for b, k, a in sent):
                continue
            pair = pairs.get((book_id, key, actor))
            if pair is None:
                pairs[(book_id, key, actor)] = Pending(self.title(book_id), role, actor, uploaded_at)
            elif uploaded_at < pair.first_at:
                pair.first_at = uploaded_at
        return sorted(pairs.values(), key=lambda p: p.first_at)

    def deadlines(self) -> tuple[list[Due], list[Due]]:
        overdue, soon = [], []
        for row in self.open_deadlines:
            ch = self.character_by_id.get(row.character_id)
            if ch is None:
                continue
            due = Due(str(row.actor_name or ""), str(row.kind), str(ch.name or ""), self.title(row.book_id), row.due_at)
            if row.due_at < self.now:
                overdue.append(due)
            elif row.due_at < self.now + SOON:
                soon.append(due)
        return overdue, soon

    def books(self) -> list[BookProgress]:
        """По книгам: «записано: X/Y» (решение владельца 05.10 вместо «глав-ролей»). Роль
        без глав в книге не считается: её нельзя записать, и счётчик не дошёл бы до конца."""
        found = []
        for book_id, cast in self.cast_by_book.items():
            recorded = approved = roles = 0
            for ch in cast:
                name, tentative = parse_actor_name(str(ch.actor_name or ""))
                done, total = self.progress(ch, name if name and not tentative else "")
                if total == 0:
                    continue
                roles += 1
                if name and not tentative:
                    approved += 1
                    recorded += done >= total
            found.append(BookProgress(self.title(book_id), recorded, approved, roles - approved, roles))
        return sorted(found, key=lambda b: b.title.lower())

    def _auditioned(self, book_id: str, role: str, actor: str) -> bool:
        key = normalize_name(role)
        return any(b == book_id and k == key and same_person(a, actor) for b, k, _, a, _ in self.audition_pairs)

    def silent(self) -> list[Silent]:
        """Утверждены и не начали — в книгах, где запись идёт; позваны и не загрузили пробу —
        в любой книге (у пробы срок 48 часов). Один человек — одна строка."""
        people: list[Silent] = []
        for row in sorted(self.open_deadlines, key=lambda r: (r.created_at, r.due_at)):
            ch = self.character_by_id.get(row.character_id)
            if ch is None:
                continue
            role, title = str(ch.name or ""), self.title(row.book_id)
            if row.kind == "role":
                if self.code_of.get(row.book_id) not in self.recording_started:
                    continue  # книгу ещё не пишут — «не начата» у всех, это не молчание
                done, total = self.progress(ch, row.actor_name)
                if total == 0 or done > 0:
                    continue
                item = f'role «{role}» («{title}») — not started'
            else:
                if self._auditioned(row.book_id, role, row.actor_name):
                    continue
                state = 'accepted, not uploaded' if row.accepted_at is not None else 'not uploaded'
                item = f'audition «{role}» («{title}») — {state}'
            person = next((p for p in people if same_person(p.actor, row.actor_name)), None)
            if person is None:
                person = Silent(actor=str(row.actor_name or ""), chat="", last_seen=None)
                people.append(person)
            person.items.append(item)
        for person in people:
            person.chat = self.chat(person.actor)
            contact = self.db.get(BotContact, person.chat) if person.chat else None
            person.last_seen = contact.last_seen_at if contact is not None else None
        return sorted(people, key=lambda p: p.actor.lower().replace("ё", "е"))


SS_PREFIX = "ss:"
_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
           'October', 'November', 'December')
SECTIONS = {"p": '🎙 Auditions awaiting a decision', "d": '⏰ Deadlines', "b": '📚 Books', "q": '🔇 Inactive'}


def _actor(name: str) -> str:
    return 'unnamed' if not name or name == UNKNOWN_ACTOR else _esc(name)


def _days(moment: datetime, now: datetime) -> str:
    return f'{max(0, (now - moment).days)} days'


def _due_line(d: Due, *, timezone: str = "") -> str:
    what = 'audition' if d.kind == "audition" else 'role'
    when = format_due(d.due_at, kind="time" if d.kind == "audition" else "role", timezone=timezone)
    return f'• {_esc(d.actor)} — {what} «{_esc(d.role)}» («{_esc(d.book_title)}»), due {when}'


def _seen(person: Silent, studio: Studio) -> str:
    """Без слов, выдающих род: «не заходил(а)» читается как канцелярит, «не заходила» — гадание."""
    if not person.chat:
        return 'Telegram not found'
    if person.last_seen is None:
        return 'no bot visits'
    return f'last bot visit {_days(person.last_seen, studio.now)} ago'


def overview(db, *, now: datetime) -> tuple[str, dict]:
    studio = Studio(db, now=now)
    pending = studio.pending()
    overdue, soon = studio.deadlines()
    from app.services.studio_calendar import local_time
    local = local_time(now, studio_timezone(db))
    lines = [f'🏢 <b>Studio — {local.day} {_MONTHS[local.month - 1]}</b>', ""]
    oldest = f' (oldest: {_days(pending[0].first_at, now)})' if pending else ""
    lines.append(f'🎙 Auditions awaiting a decision: {len(pending)}{oldest}')
    lines.append(f'⏰ Deadlines: overdue {len(overdue)} · upcoming {len(soon)}')
    for book in studio.books():
        lines.append(f'📚 {_esc(book.title)} — recorded: {book.recorded}/{book.roles}')
    lines.append(f'🔇 Inactive: {len(studio.silent())}')
    buttons = [[{"text": 'Auditions ▸', "callback_data": f"{SS_PREFIX}p"}, {"text": 'Deadlines ▸', "callback_data": f"{SS_PREFIX}d"}],
               [{"text": 'Books ▸', "callback_data": f"{SS_PREFIX}b"}, {"text": 'Inactive ▸', "callback_data": f"{SS_PREFIX}q"}]]
    return _fit(lines), {"inline_keyboard": buttons}


def section(db, key: str, *, now: datetime) -> str:
    if key not in SECTIONS:
        return ""
    studio = Studio(db, now=now)
    lines = [f"<b>{SECTIONS[key]}</b>", ""]
    if key == "p":
        pending = studio.pending()
        by_book: dict[str, list[Pending]] = {}
        for p in pending:
            by_book.setdefault(p.book_title, []).append(p)
        for title, items in by_book.items():
            lines.append(f"«{_esc(title)}»:")
            lines += [f"• «{_esc(p.role)}» — {_actor(p.actor)}, {_days(p.first_at, now)}" for p in items]
        if not pending:
            lines.append('None.')
    elif key == "d":
        overdue, soon = studio.deadlines()
        lines += ['Overdue:'] + ([_due_line(d, timezone=studio_timezone(db)) for d in overdue] or ['none'])
        lines += ["", 'Due soon (3 days):'] + ([_due_line(d, timezone=studio_timezone(db)) for d in soon] or ['none'])
    elif key == "b":
        for b in studio.books():
            lines.append(f'• «{_esc(b.title)}» — recorded: {b.recorded}/{b.roles} · approved: {b.approved} · available: {b.free}')
    else:
        silent = studio.silent()
        for person in silent:
            nick = ""
            if person.chat:
                contact = db.get(BotContact, person.chat)
                nick = f" (@{_esc(contact.username)})" if contact is not None and contact.username else ""
            lines.append(f"• {_esc(person.actor)}{nick} — {_seen(person, studio)}")
            lines += [f"   {_esc(item)}" for item in person.items]
        if not silent:
            lines.append('None.')
    return _fit(lines)


DC_PREFIX = "dc:"
#: больше кнопок — уже не выбор, а список; просим уточнить
MAX_PICKS = 8


@dataclass(frozen=True)
class Person:
    name: str
    tid: str            # "" — актёр есть только в касте


def _alike(a: str, b: str) -> bool:
    return bool(a.strip()) and bool(b.strip()) and (names_match(a, b) or names_match(b, a))


def _account_names(db, account: TelegramAuthAccount) -> list[str]:
    user = db.get(User, str(account.user_id or "")) if str(account.user_id or "").strip() else None
    names = [str(account.display_name or ""), str(user.display_name or "") if user is not None else ""]
    return [name for name in dict.fromkeys(names) if name.strip()]


def find_people(db, query: str) -> list[Person]:
    """Учётки с Telegram и актёры каста, чьё имя похоже на запрос.

    Имя из каста сливается с учёткой, только если указывает на неё так же, как для писем
    (`chat_for`), — по этому же правилу карточка находит роли. Ревью 05.10: нечёткое «похоже
    на любую учётку» отдавало «Иванову Марию» из каста учётке «Мария» — у той пустая
    карточка, а роли настоящей Марии не показывались никому."""
    found: dict[str, Person] = {}
    by_tid: dict[str, Person] = {}
    for account in db.query(TelegramAuthAccount).filter(TelegramAuthAccount.is_active == "true").all():
        names = _account_names(db, account)
        tid = str(account.telegram_user_id or "").strip()
        if not tid or not names:
            continue
        person = Person(name=names[-1], tid=tid)
        by_tid.setdefault(tid, person)
        if tid not in found and any(_alike(query, n) for n in names):
            found[tid] = person
    cast_only: dict[str, Person] = {}
    for ch in db.query(Character).all():
        name, _ = parse_actor_name(str(ch.actor_name or ""))
        if not name or not _alike(query, name):
            continue
        tid = chat_for(db, name)
        if tid in by_tid:
            found.setdefault(tid, by_tid[tid])  # имя каста — этот человек с Telegram
            continue
        if not any(same_person(name, p.name) for p in cast_only.values()):
            cast_only[name] = Person(name=name, tid="")
    return sorted([*found.values(), *cast_only.values()], key=lambda p: p.name.lower())


def _card(db, person: Person, *, now: datetime) -> str:
    if person.tid:
        account = (db.query(TelegramAuthAccount)
                   .filter(TelegramAuthAccount.telegram_user_id == person.tid).first())
        user = db.get(User, str(account.user_id or "")) if account is not None and account.user_id else None
        if user is None:
            user = db.query(User).filter(User.login == f"tg_{person.tid}").first()
        owns = owner_by_chat(db, person.tid, user)
        contact = db.get(BotContact, person.tid)
        head = [f"👤 <b>{_esc(person.name)}</b>"]
        if user is not None:
            head.append(f'Login: <code>{_esc(user.login)}</code>')
        nick = f"@{_esc(contact.username)}" if contact is not None and contact.username else 'no username'
        seen = f'last bot visit {max(0, (now - contact.last_seen_at).days)} days ago' if contact is not None else 'no bot visits'
        head.append(f"Telegram: {nick} · {seen}")
    else:
        owns = owner_by_name(person.name)
        head = [f"👤 <b>{_esc(person.name)}</b>", 'No Telegram account linked; listed only in the cast.']
    lines = person_lines(db, owns, now=now, you=False)
    parts = head + ["", '🎬 <b>Approved roles</b>'] + (lines.roles or ['None.'])
    parts += ["", '🎙 <b>Auditions</b>'] + (lines.auditions or ['None.'])
    if lines.rejected:
        parts += ["", '✉️ <b>Other selections</b>'] + lines.rejected
    return _fit(parts)


def dictor_reply(db, query: str, *, now: datetime) -> tuple[str, dict | None]:
    query = str(query or "").strip()
    if not query:
        return 'Enter: /dictor Surname', None
    people = find_people(db, query)
    if not people:
        return f'No match for «{_esc(query)}».', None
    if len(people) == 1:
        return _card(db, people[0], now=now), None
    if len(people) > MAX_PICKS:
        return f'The name «{_esc(query)}» matches {len(people)} people; please specify the name.', None
    rows = []
    for p in people:
        if p.tid:
            rows.append([{"text": p.name[:60], "callback_data": f"{DC_PREFIX}t:{p.tid}"}])
        else:
            # у актёра без Telegram нет id — кнопка несёт персонажа, по нему имя восстановится
            ch = next(c for c in db.query(Character).all()
                      if same_person(parse_actor_name(str(c.actor_name or ""))[0], p.name))
            rows.append([{"text": f'{p.name[:50]} (cast)', "callback_data": f"{DC_PREFIX}c:{ch.id}"}])
    return 'Who would you like to view?', {"inline_keyboard": rows}


def card_by_pick(db, data: str, *, now: datetime) -> str:
    kind, _, value = data[len(DC_PREFIX):].partition(":")
    if kind == "t" and value:
        account = (db.query(TelegramAuthAccount)
                   .filter(TelegramAuthAccount.telegram_user_id == value, TelegramAuthAccount.is_active == "true").first())
        if account is None:
            return 'Account not found.'
        names = _account_names(db, account)
        return _card(db, Person(name=names[-1] if len(names) > 1 else (names[0] if names else value), tid=value), now=now)
    if kind == "c" and value:
        ch = db.get(Character, value)
        name = parse_actor_name(str(ch.actor_name or ""))[0] if ch is not None else ""
        return _card(db, Person(name=name, tid=""), now=now) if name else 'Actor not found.'
    return ""
