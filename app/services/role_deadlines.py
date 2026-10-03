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

from datetime import date, datetime, timedelta

from app.models import AudioFile, Character, RoleDeadline, ScriptBook, ScriptChapter
from app.services.audio_uploads import derive_book_code
from app.services.shared_runtime import is_narrator_name
from app.services.telegram import names_match
from app.time_utils import utcnow_naive
from app.v2.cast_ops import parse_actor_name
from app.services import studio

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
    return _end_of_msk_day(day)


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


def role_chapters(db, character: Character) -> dict[int, str]:
    """Номер главы → её название (по нему дубль привязан к главе). Глав, которых нет в
    книге, здесь нет: иначе роль нельзя было бы сдать никогда."""
    wanted = {int(part) for part in str(character.appears_in or "").replace(";", ",").split(",")
              if part.strip().isdigit()}
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
    recorded = {
        str(item.chapter or "").strip()
        for item in db.query(AudioFile).filter(
            AudioFile.kind == "take", AudioFile.book_code == code,
            AudioFile.role == str(character.name or ""), AudioFile.chapter.in_(sorted(titles)),
        ).all()
        if same_person(str(item.actor_name or ""), actor)
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
    for ch in db.query(Character).filter(Character.book_id == book_id).all():
        if is_narrator_name(str(ch.name or "")):
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


_MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября",
           "октября", "ноября", "декабря")


def format_due(due_at: datetime, *, kind: str) -> str:
    """Срок для письма: проба — «3 октября, 18:00 МСК (48 часов)», роль — «31 декабря 2026»."""
    local = due_at + MSK
    if kind == "audition":
        return f"{local.day} {_MONTHS[local.month - 1]}, {local:%H:%M} МСК ({AUDITION_HOURS} часов)"
    if kind == "time":  # продлённая проба: дата и время, без «48 часов»
        return f"{local.day} {_MONTHS[local.month - 1]}, {local:%H:%M} МСК"
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}"


DIGEST_HOUR_MSK = 10


def _done(db, row: RoleDeadline, character: Character) -> bool:
    if row.kind == "audition":
        book = db.get(ScriptBook, character.book_id)
        code = derive_book_code(str(getattr(book, "title", "") or ""))
        return any(
            same_person(str(item.actor_name or ""), row.actor_name)
            for item in db.query(AudioFile).filter(
                AudioFile.kind == "audition", AudioFile.book_code == code,
                AudioFile.role == str(character.name or ""), AudioFile.uploaded_at >= row.created_at,
            ).all()
        )
    done, total = role_progress(db, character, row.actor_name)
    return bool(total) and done >= total


def chat_for(db, actor: str) -> str:
    """Чат актёра, только если имя указывает ровно на одного человека — как у писем о
    назначении: при двух «Ольгах» письмо не уходит никому (ревью 01.10)."""
    from app.services.role_approval import resolve_actor_candidates

    candidates = resolve_actor_candidates(db, actor)
    return candidates[0][0] if len(candidates) == 1 else ""


def recast_notice(*, role: str, book_title: str) -> str:
    return (f"Здравствуйте! К сожалению, на роль «{role}» («{book_title}») выбран другой исполнитель. "
            "Извините — это рекаст. Спасибо за работу, надеемся на новые роли.")


def _reminder(row: RoleDeadline, *, role: str, book_title: str, progress: tuple[int, int], overdue: bool) -> str:
    what = f"проба на роль «{role}»" if row.kind == "audition" else f"роль «{role}»"
    if overdue:
        return f"Срок: {what} («{book_title}») — срок вышел. Если нужно больше времени — {studio.write_to()}."
    lines = [f"Напоминаем: {what} («{book_title}») — до {format_due(row.due_at, kind='role' if row.kind == 'role' else 'time')}."]
    done, total = progress
    if row.kind == "role" and total:
        lines.append(f"Сдано {done} из {total} глав.")
    return "\n".join(lines)


def run_pass(db, *, now: datetime | None = None, send) -> dict:
    """Один проход: закрыть сданное, письма о рекасте, напоминания, утренняя сводка.

    `send(chat_id, text) -> bool`. Каждое письмо — один раз: отметка ставится и тогда,
    когда адресата нет или Telegram не принял — повторы по таймеру были бы спамом.
    """
    from app.config import settings
    from app.services.studio_settings import studio_settings

    now = now or utcnow_naive()
    stats = {"done": 0, "recast": 0, "before": 0, "overdue": 0, "digest": 0}
    titles = {book.id: str(book.title or "") for book in db.query(ScriptBook).all()}

    for row in db.query(RoleDeadline).filter(RoleDeadline.closed_at.is_(None)).all():
        character = db.get(Character, row.character_id)
        if character is None:
            _close(row, "gone", now)
            continue
        if _done(db, row, character):
            _close(row, "done", now)
            stats["done"] += 1

    db.commit()  # закрытое сданным — до писем
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

    open_rows = db.query(RoleDeadline).filter(RoleDeadline.closed_at.is_(None)).all()
    for row in open_rows:
        character = db.get(Character, row.character_id)
        role, book_title = str(getattr(character, "name", "") or ""), titles.get(row.book_id, "")
        if now >= row.due_at and row.reminded_overdue_at is None:
            row.reminded_overdue_at = now
            row.reminded_before_at = row.reminded_before_at or now
            db.commit()
            chat = chat_for(db, row.actor_name)
            if chat:
                send(chat, _reminder(row, role=role, book_title=book_title, progress=(0, 0), overdue=True))
                stats["overdue"] += 1
        elif row.due_at - timedelta(hours=24) <= now < row.due_at and row.reminded_before_at is None:
            row.reminded_before_at = now
            db.commit()
            chat = chat_for(db, row.actor_name)
            if chat:
                progress = role_progress(db, character, row.actor_name) if character is not None else (0, 0)
                send(chat, _reminder(row, role=role, book_title=book_title, progress=progress, overdue=False))
                stats["before"] += 1

    local = now + MSK
    owner = str(settings.owner_telegram_id or "").strip()
    row_settings = studio_settings(db)
    today = local.strftime("%Y-%m-%d")  # день по Москве, не время из базы
    if owner and local.hour == DIGEST_HOUR_MSK and str(row_settings.deadline_digest_day or "") != today:
        day_end = _end_of_msk_day(local.date())
        due_today, overdue = [], []
        for row in open_rows:
            if row.closed_at is not None:
                continue
            character = db.get(Character, row.character_id)
            label = f"{row.actor_name} — «{getattr(character, 'name', '')}» («{titles.get(row.book_id, '')}»)"
            if row.kind == "role" and character is not None:
                done, total = role_progress(db, character, row.actor_name)
                if total:
                    label += f", {done}/{total} гл."
            if row.kind == "audition":
                label += ", проба"
            if row.due_at < now:
                overdue.append(label)
            elif row.due_at <= day_end:
                due_today.append(label)
        if due_today or overdue:
            parts = ["Сроки на сегодня."]
            if due_today:
                parts.append("Истекает сегодня:\n" + "\n".join(f"• {item}" for item in due_today))
            if overdue:
                parts.append("Просрочено:\n" + "\n".join(f"• {item}" for item in overdue))
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
        new_due = _end_of_msk_day(day)
    else:
        if not days or int(days) <= 0 or int(days) > 365:
            raise DeadlineError("bad_days")
        new_due = max(row.due_at, now) + timedelta(days=int(days))
    if new_due <= now:
        raise DeadlineError("bad_date")
    row.due_at = new_due
    row.reminded_before_at = None
    row.reminded_overdue_at = None
    row.extended_by = str(by or "")[:120]
    db.flush()
    return row


def close_manual(db, deadline_id: str, *, now: datetime | None = None) -> RoleDeadline:
    row = _open_row(db, deadline_id)
    _close(row, "manual", now or utcnow_naive())
    db.flush()
    return row


def extension_notice(row: RoleDeadline, *, role: str, book_title: str) -> str:
    what = f"проба на роль «{role}»" if row.kind == "audition" else f"роль «{role}»"
    return f"Срок продлён: {what} («{book_title}») — до {format_due(row.due_at, kind='role' if row.kind == 'role' else 'time')}."
