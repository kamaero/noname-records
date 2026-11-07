"""Сроки проб и утверждённых ролей (спека 2026-10-01-role-deadlines).

Решения владельца: проба — 48 часов с приглашения; утверждённая роль — до общей даты
студии (`studio_settings.role_deadline_date`, конец дня по Москве); роль сдана, когда во
всех её главах лежит дубль диктора; проба сдана, когда загружена проба.

Сверка (`sync_book`) идёт из `casting.rebuild_assignments` — его зовут все пути смены
актёра, поэтому срок не зависит от того, откуда пришло назначение. Письма и напоминания
шлёт проход `run_pass` под таймером, а не сверка: она зовётся из многих мест, в том числе
из конвейера, и посылать оттуда в Telegram нельзя.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from app.models import AudioFile, Character, RoleDeadline, ScriptBook, ScriptChapter
from app.services import bot_out, studio
from app.services.audio_uploads import derive_book_code
from app.services.role_identity import book_roles
from app.services.telegram import names_match
from app.time_utils import utcnow_naive
from app.services.studio_calendar import end_of_day, local_time, studio_timezone, timezone_label
from app.v2.cast_ops import parse_actor_name

logger = logging.getLogger(__name__)

AUDITION_HOURS = 48
#: закрытые так сроки не открываются заново для того же актёра того же вида
SETTLED = ("done", "manual", "baseline")
#: письмо о рекасте ждёт: опечатку в касте успевают исправить, и тогда письма нет
RECAST_GRACE = timedelta(minutes=30)
MSK = timedelta(hours=3)
FALLBACK_ROLE_DATE = "2026-12-31"


def same_person(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (names_match(a, b) or names_match(b, a))


def _end_of_msk_day(day: date) -> datetime:
    """23:59:59 по Москве в наивном UTC, как всё время в базе."""
    return datetime(day.year, day.month, day.day, 23, 59, 59) - MSK


def role_due_date(db) -> datetime:
    from app.services.studio_settings import studio_settings

    raw = str(getattr(studio_settings(db), "role_deadline_date", "") or FALLBACK_ROLE_DATE)
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        day = date.fromisoformat(FALLBACK_ROLE_DATE)
    return end_of_day(day, studio_timezone(db))


def due_for(db, kind: str, *, now: datetime) -> datetime:
    if kind == "audition":
        return now + timedelta(hours=AUDITION_HOURS)
    return role_due_date(db)


def open_deadline(db, character_id: str) -> RoleDeadline | None:
    return (db.query(RoleDeadline)
            .filter(RoleDeadline.character_id == character_id, RoleDeadline.closed_at.is_(None))
            .order_by(RoleDeadline.created_at.desc()).first())


def _latest(db, character_id: str) -> RoleDeadline | None:
    return (db.query(RoleDeadline).filter(RoleDeadline.character_id == character_id)
            .order_by(RoleDeadline.created_at.desc(), RoleDeadline.id.desc()).first())


def appears_in_chapters(character) -> set[int]:
    """Номера глав из графы `appears_in` — запасной ответ для книги без разметки."""
    return {int(part) for part in str(getattr(character, "appears_in", "") or "").replace(";", ",").split(",")
            if part.strip().isdigit()}


def wanted_chapters(db, character) -> set[int]:
    """Главы, где роль говорит: по текущей разметке, а без разметки — по `appears_in`
    (почему не наоборот — см. `role_chapter_map`)."""
    from app.v2.cast_ops import role_chapter_map

    from app.services.role_identity import book_roles, RoleIdentityError
    identities = book_roles(db, character.book_id)
    if identities.typed:
        from app.v2.models import V2Segment
        from app.v2.reader import effective_attributions
        rows = db.query(V2Segment.id, ScriptChapter.chapter_index).join(
            ScriptChapter, ScriptChapter.id == V2Segment.chapter_id).filter(
            V2Segment.book_id == character.book_id).all()
        if not rows:
            return appears_in_chapters(character)
        expected = identities.character_ref(character)
        attrs = effective_attributions(db, [sid for sid, _index in rows])
        wanted = set()
        for sid, index in rows:
            for attr in attrs.get(sid, []):
                ref = str(getattr(attr, "role_ref", "") or "")
                if not ref:
                    try:
                        ref = identities.ref(attr.speaker)
                    except RoleIdentityError:
                        continue
                if ref == expected:
                    wanted.add(int(index))
        return wanted
    marked = role_chapter_map(db, str(character.book_id or ""))
    if marked is None:
        return appears_in_chapters(character)
    return set(marked.get(str(getattr(character, "id", "") or ""), set()))


def role_chapters(db, character: Character) -> dict[int, str]:
    """Номер главы → её название (по нему дубль привязан к главе). Глав, которых нет в
    книге, здесь нет: иначе роль нельзя было бы сдать никогда."""
    wanted = wanted_chapters(db, character)
    if not wanted:
        return {}
    from app.services.chapter_delivery import _chapter_label

    rows = db.query(ScriptChapter).filter(ScriptChapter.book_id == character.book_id,
                                          ScriptChapter.chapter_index.in_(sorted(wanted))).all()
    # метка — та же, под которой дубль привязан к главе (у главы без названия — «Глава N»)
    return {int(row.chapter_index): _chapter_label(row) for row in rows}


def role_progress(db, character: Character, actor: str) -> tuple[int, int]:
    """(глав с дублем этого актёра, всего глав роли)."""
    chapters = role_chapters(db, character)
    if not chapters:
        return 0, 0
    book = db.get(ScriptBook, character.book_id)
    code = derive_book_code(str(getattr(book, "title", "") or ""))
    titles = set(chapters.values())
    from app.services.role_identity import book_roles
    identities = book_roles(db, character.book_id)
    recorded = {
        str(item.chapter or "").strip()
        for item in db.query(AudioFile).filter(
            AudioFile.kind == "take", AudioFile.chapter.in_(sorted(titles)),
        ).all()
        if identities.matches_character_audio(item, character) and same_person(str(item.actor_name or ""), actor)
    }
    # считаем по номерам глав: две главы с одинаковым названием — всё равно две
    return sum(1 for label in chapters.values() if label in recorded), len(chapters)


def _close(row: RoleDeadline, reason: str, now: datetime) -> None:
    row.closed_at = now
    row.close_reason = reason


def needs_recast_notice(row: RoleDeadline) -> bool:
    """С утверждённой роли сняли (заменили или очистили) — письмо «извините, рекаст»."""
    return (row.kind == "role" and row.close_reason in ("replaced", "removed")
            and row.recast_notified_at is None)


def sync_book(db, book_id: str, *, now: datetime | None = None) -> dict:
    """Привести сроки книги в соответствие с кастом. Повторный вызов ничего не меняет."""
    now = now or utcnow_naive()
    opened = closed = 0

    book = db.get(ScriptBook, book_id)
    if book is not None and book.voiced_at is not None:
        # В озвученной книге сроки не открываем: ролям записывать больше нечего.
        return {"opened": 0, "closed": 0}
    # «Не смогу» держит пробу закрытой, только пока каст не менялся (ревью пакета B): сняли
    # человека или отдали роль другому — отказ гаснет, и новое приглашение того же человека
    # (ошибся кнопкой, передумал) откроет новый срок.
    declined = {row.character_id: row for row in db.query(RoleDeadline).filter(
        RoleDeadline.book_id == book_id, RoleDeadline.close_reason == "declined")}
    identity = book_roles(db, str(book_id))
    for ch in db.query(Character).filter(Character.book_id == book_id).all():
        if identity.is_narrator(str(ch.name or "")):
            continue
        name, tentative = parse_actor_name(str(ch.actor_name or ""))
        kind = "audition" if tentative else "role"
        current = open_deadline(db, ch.id)
        if current is not None:
            if name and same_person(current.actor_name, name) and current.kind == kind:
                continue
            if not name:
                reason = "removed"
            elif same_person(current.actor_name, name):
                reason = "changed"  # тот же человек: проба → роль или наоборот
            else:
                reason = "replaced"
            _close(current, reason, now)
            closed += 1
            if reason == "replaced":
                # Роль ушла другому актёру: если она говорит в сданных главах, владелец
                # должен знать. Сбой письма не должен ломать сверку сроков.
                try:
                    from app.services.released_alerts import notify_recast_in_released

                    notify_recast_in_released(db, ch, old_actor=current.actor_name, new_actor=name)
                except Exception:
                    logger.exception("Не удалось сообщить о рекасте в сданной главе")
        if not name:
            continue
        if current is not None and current.kind == "role" and kind == "audition" \
                and same_person(current.actor_name, name):
            continue  # утверждённого вернули в «Имя?» — на пробу его не звали, письма не было
        latest = _latest(db, ch.id)
        if latest is not None and latest is not current and same_person(latest.actor_name, name) \
                and latest.kind == kind and latest.close_reason in SETTLED:
            continue  # уже был срок того же актёра того же вида: сдан, закрыт руками, baseline
        if kind == "role":
            done, total = role_progress(db, ch, name)
            if not total or done >= total:
                continue  # глав роли не знаем — сдать нельзя; или уже всё записано
        db.add(RoleDeadline(character_id=ch.id, book_id=book_id, actor_name=name, kind=kind,
                            due_at=due_for(db, kind, now=now), created_at=now))
        opened += 1
        db.flush()
    db.flush()
    return {"opened": opened, "closed": closed}


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
           "October", "November", "December")


def format_due(due_at: datetime, *, kind: str, timezone: str = "") -> str:
    """Срок для письма: проба — «3 октября, 18:00 МСК (48 часов)», роль — «31 декабря 2026»."""
    local = local_time(due_at, timezone)
    label = timezone_label(timezone, due_at)
    if kind == "audition":
        return f"{local.day} {_MONTHS[local.month - 1]}, {local:%H:%M} {label} ({AUDITION_HOURS} hours)"
    if kind == "time":  # продлённая проба: дата и время, без «48 часов»
        return f"{local.day} {_MONTHS[local.month - 1]}, {local:%H:%M} {label}"
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}"


DIGEST_HOUR_MSK = 10


def _done(db, row: RoleDeadline, character: Character) -> bool:
    if row.kind == "audition":
        book = db.get(ScriptBook, character.book_id)
        code = derive_book_code(str(getattr(book, "title", "") or ""))
        from app.services.role_identity import book_roles
        identities = book_roles(db, character.book_id)
        return any(
            identities.matches_character_audio(item, character) and same_person(str(item.actor_name or ""), row.actor_name)
            for item in db.query(AudioFile).filter(
                AudioFile.kind == "audition", AudioFile.uploaded_at >= row.created_at,
            ).all()
        )
    done, total = role_progress(db, character, row.actor_name)
    if not total:
        # По разметке у роли реплик не осталось (их перенесли другим ролям) — записывать
        # нечего, а открытый срок прислал бы «срок истёк». Книга без разметки сюда не
        # попадает: там глав не знаем вовсе, и сдать роль нельзя, как и раньше.
        from app.v2.cast_ops import role_chapter_map

        return role_chapter_map(db, str(character.book_id or "")) is not None
    return done >= total


def done_notice(*, actor: str, role: str, book_title: str, done: int, total: int) -> str:
    """Владельцу: роль записана целиком, пора платить остаток гонорара. Род по имени не
    угадываем — фраза безличная."""
    return f"Role «{role}» («{book_title}») is fully recorded: {actor}, {done} of {total} chapters. Payment can be calculated."


def chat_for(db, actor: str) -> str:
    """Чат актёра, только если имя указывает ровно на одного человека — как у писем о
    назначении: при двух «Ольгах» письмо не уходит никому (ревью 01.10)."""
    from app.services.role_approval import resolve_actor_candidates

    candidates = resolve_actor_candidates(db, actor)
    return candidates[0][0] if len(candidates) == 1 else ""


def recast_notice(*, role: str, book_title: str) -> str:
    return (f"Hello. Another actor has been selected for the role «{role}» («{book_title}»). "
            "This role has been recast. Thank you for your work; we hope to work with you on future roles.")


def _reminder(row: RoleDeadline, *, role: str, book_title: str, progress: tuple[int, int], overdue: bool,
               timezone: str = "") -> str:
    what = f"audition for «{role}»" if row.kind == "audition" else f"role «{role}»"
    if overdue:
        return f"Deadline: {what} («{book_title}») is overdue. If you need more time, {studio.write_to()}."
    lines = [f"Reminder: {what} («{book_title}») — due {format_due(row.due_at, kind='role' if row.kind == 'role' else 'time', timezone=timezone)}."]
    done, total = progress
    if row.kind == "role" and total:
        lines.append(f"Completed {done} of {total} chapters.")
    return "\n".join(lines)


def run_pass(db, *, now: datetime | None = None, send, send_buttons=None) -> dict:
    """Один проход: закрыть сданное, письма о рекасте, напоминания, утренняя сводка.

    `send(chat_id, text) -> bool`. `send_buttons(chat_id, text, markup) -> bool` — для
    напоминаний, где нужна кнопка «Нужно больше времени». Каждое письмо — один раз:
    отметка ставится и тогда, когда адресата нет или Telegram не принял — повторы по
    таймеру были бы спамом.
    """
    from app.config import settings
    from app.services.studio_settings import studio_settings

    now = now or utcnow_naive()
    stats = {"done": 0, "done_notified": 0, "recast": 0, "before": 0, "overdue": 0, "digest": 0}
    titles = {book.id: str(book.title or "") for book in db.query(ScriptBook).all()}

    voiced = {book.id for book in db.query(ScriptBook).filter(ScriptBook.voiced_at.isnot(None))}
    finished: list[str] = []  # письма владельцу о целиком записанных ролях
    for row in db.query(RoleDeadline).filter(RoleDeadline.closed_at.is_(None)).all():
        if row.book_id in voiced:
            # Книгу озвучили, а срок остался открытым (voice_book закрывает их сам, но
            # книга могла стать озвученной и иначе): закрываем молча, без письма.
            _close(row, "voiced", now)
            continue
        character = db.get(Character, row.character_id)
        if character is None:
            _close(row, "gone", now)
            continue
        if _done(db, row, character):
            _close(row, "done", now)
            stats["done"] += 1
            if row.kind == "role":
                done, total = role_progress(db, character, row.actor_name)
                if total:  # роль без реплик закрылась не записью — рассчитывать не за что
                    finished.append(done_notice(actor=row.actor_name, role=str(character.name or ""),
                                                book_title=titles.get(row.book_id, ""), done=done, total=total))

    db.commit()  # закрытое сданным — до писем
    owner = str(settings.owner_telegram_id or "").strip()
    if owner:
        # Отметка — сам закрытый срок, он уже в базе: письмо уходит один раз, а повторно
        # роль закроется, только если срок откроют снова (добавились главы).
        for text in finished:
            send(owner, text)
            stats["done_notified"] += 1
    for row in db.query(RoleDeadline).filter(RoleDeadline.kind == "role",
                                             RoleDeadline.recast_notified_at.is_(None),
                                             RoleDeadline.close_reason.in_(["replaced", "removed"]),
                                             RoleDeadline.closed_at <= now - RECAST_GRACE).all():
        character = db.get(Character, row.character_id)
        # Отметка — до отправки и с коммитом: упавший проход не повторит письмо.
        row.recast_notified_at = now
        db.commit()
        if character is None:
            continue
        holder, _ = parse_actor_name(str(character.actor_name or ""))
        back = same_person(holder, row.actor_name) or (
            (other := open_deadline(db, character.id)) is not None and same_person(other.actor_name, row.actor_name))
        if back:
            continue  # опечатку исправили — человек снова на роли
        chat = chat_for(db, row.actor_name)
        if chat:
            send(chat, recast_notice(role=str(character.name or ""), book_title=titles.get(row.book_id, "")))
            stats["recast"] += 1

    def _push_reminder(row, text):
        chat = chat_for(db, row.actor_name)
        if not chat:
            return
        if send_buttons is not None and row.character_id:
            send_buttons(chat, text, bot_out.time_markup(row.character_id))
        else:
            send(chat, text)

    open_rows = db.query(RoleDeadline).filter(RoleDeadline.closed_at.is_(None)).all()
    for row in open_rows:
        character = db.get(Character, row.character_id)
        role, book_title = str(getattr(character, "name", "") or ""), titles.get(row.book_id, "")
        if now >= row.due_at and row.reminded_overdue_at is None:
            row.reminded_overdue_at = now
            row.reminded_before_at = row.reminded_before_at or now
            db.commit()
            _push_reminder(row, _reminder(row, role=role, book_title=book_title, progress=(0, 0), overdue=True, timezone=studio_timezone(db)))
            stats["overdue"] += 1
        elif row.due_at - timedelta(hours=24) <= now < row.due_at and row.reminded_before_at is None:
            row.reminded_before_at = now
            db.commit()
            progress = role_progress(db, character, row.actor_name) if character is not None else (0, 0)
            _push_reminder(row, _reminder(row, role=role, book_title=book_title, progress=progress, overdue=False, timezone=studio_timezone(db)))
            stats["before"] += 1

    local = local_time(now, studio_timezone(db))
    row_settings = studio_settings(db)
    today = local.strftime("%Y-%m-%d")  # день по Москве, не время из базы
    if owner and local.hour == DIGEST_HOUR_MSK and str(row_settings.deadline_digest_day or "") != today:
        day_end = end_of_day(local.date(), studio_timezone(db))
        due_today, overdue = [], []
        for row in open_rows:
            if row.closed_at is not None:
                continue
            character = db.get(Character, row.character_id)
            label = f"{row.actor_name} — «{getattr(character, 'name', '')}» («{titles.get(row.book_id, '')}»)"
            if row.kind == "role" and character is not None:
                done, total = role_progress(db, character, row.actor_name)
                if total:
                    label += f", {done}/{total} ch."
            if row.kind == "audition":
                label += ", audition"
            if row.due_at < now:
                overdue.append(label)
            elif row.due_at <= day_end:
                due_today.append(label)
        if due_today or overdue:
            parts = ["Deadlines today."]
            if due_today:
                parts.append("Due today:\n" + "\n".join(f"• {item}" for item in due_today))
            if overdue:
                parts.append("Overdue:\n" + "\n".join(f"• {item}" for item in overdue))
            row_settings.deadline_digest_day = today
            db.commit()
            send(owner, "\n\n".join(parts))
            stats["digest"] = 1
    db.flush()
    return stats


class DeadlineError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def deadline_view(db, row: RoleDeadline, *, now: datetime | None = None) -> dict:
    now = now or utcnow_naive()
    character = db.get(Character, row.character_id)
    done, total = role_progress(db, character, row.actor_name) if (character is not None and row.kind == "role") else (0, 0)
    from app.time_utils import iso_utc

    return {"id": row.id, "kind": row.kind, "due_at": iso_utc(row.due_at), "overdue": now >= row.due_at,
            "done": done, "total": total}


def book_views(db, book_id: str) -> dict[str, dict]:
    """Открытые сроки книги по персонажу — для каста."""
    return {row.character_id: deadline_view(db, row)
            for row in db.query(RoleDeadline).filter(RoleDeadline.book_id == book_id,
                                                     RoleDeadline.closed_at.is_(None)).all()}


def _open_row(db, deadline_id: str) -> RoleDeadline:
    row = db.get(RoleDeadline, str(deadline_id or ""))
    if row is None or row.closed_at is not None:
        raise DeadlineError("not_found")
    return row


def extend(db, deadline_id: str, *, days: int | None = None, until: str = "", by: str = "",
           now: datetime | None = None) -> RoleDeadline:
    """Продлить на N дней (от прежнего срока или от сейчас — что позже) или до даты (конец дня МСК).
    Напоминания сбрасываются: у нового срока они свои."""
    now = now or utcnow_naive()
    row = _open_row(db, deadline_id)
    if until:
        try:
            day = date.fromisoformat(str(until))
        except ValueError as exc:
            raise DeadlineError("bad_date") from exc
        new_due = end_of_day(day, studio_timezone(db))
    else:
        if not days or int(days) <= 0 or int(days) > 365:
            raise DeadlineError("bad_days")
        timezone = studio_timezone(db)
        if timezone and row.kind == "role":
            # В новой IANA-установке дата роли означает конец местного дня:
            # прибавление 24 ч в UTC сдвигало бы его на час при переходе DST.
            day = local_time(max(row.due_at, now), timezone).date() + timedelta(days=int(days))
            new_due = end_of_day(day, timezone)
        else:
            new_due = max(row.due_at, now) + timedelta(days=int(days))
    if new_due <= now:
        raise DeadlineError("bad_date")
    row.due_at = new_due
    row.reminded_before_at = None
    row.reminded_overdue_at = None
    row.extended_by = str(by or "")[:120]
    row.extend_request_days = None
    row.extend_request_at = None
    db.flush()
    return row


def close_manual(db, deadline_id: str, *, now: datetime | None = None) -> RoleDeadline:
    row = _open_row(db, deadline_id)
    _close(row, "manual", now or utcnow_naive())
    db.flush()
    return row


def extension_notice(row: RoleDeadline, *, role: str, book_title: str, timezone: str = "") -> str:
    what = f"audition for «{role}»" if row.kind == "audition" else f"role «{role}»"
    return f"Deadline extended: {what} («{book_title}») — due {format_due(row.due_at, kind='role' if row.kind == 'role' else 'time', timezone=timezone)}."
