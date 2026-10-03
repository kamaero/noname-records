"""Знакомство в боте: человек сам называет фамилию и имя, бот заводит учётку диктора.

Решения владельца (01.10): имя принимается сразу, без проверки; представиться может любой,
кто пришёл по ссылке-приглашению. Учётка — такая же, какую заводит импорт голосов
(`tg_<id>` + привязка телеграма), поэтому импорт и бот друг другу не мешают. Чужие учётки
не трогаются никогда: совпадение имени — повод написать владельцу, а не привязать.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from app.models import BotIntro, ScriptBook, TelegramAuthAccount, User
from app.services.login_identity import TELEGRAM_PASSWORD_HASH
from app.services.telegram import names_match
from app.services.user_admin import TELEGRAM_LOGIN_PREFIX, attach_telegram, create_user
from app.services import studio

INTRO_TTL = timedelta(days=1)
NAME_MAX = 60
ASK_FIRST = (f"Здравствуйте! Это бот студии {studio.name()}. Чтобы открыть вам доступ к сайту, напишите, "
             "пожалуйста, фамилию и имя — например: Ветрова Ольга.")

# Слово — буквы любого алфавита, внутри можно дефис или апостроф: «Римский-Корсаков», «O'Brien».
_WORD = re.compile(r"^[^\W\d_]+(?:[-'’][^\W\d_]+)*$")


def clean_name(text: str) -> str:
    """«ветрова  ольга» → «Ветрова Ольга». Пусто — не имя (тогда бот переспрашивает)."""
    words = str(text or "").split()
    if not 2 <= len(words) <= 4 or not all(_WORD.match(word) for word in words):
        return ""
    name = " ".join(word[:1].upper() + word[1:] for word in words)
    return name if len(name) <= NAME_MAX else ""


def _login(telegram_user_id: str) -> str:
    return f"{TELEGRAM_LOGIN_PREFIX}{telegram_user_id}"


def may_introduce(db, telegram_user_id: str) -> bool:
    """Нет учётки `tg_<id>`, и строки белого списка нет — или она активна и ни к кому не
    привязана. Выключенную строку выключили нарочно, привязанная — уже чья-то."""
    tid = str(telegram_user_id or "").strip()
    if not tid.isdigit():
        return False
    if db.query(User).filter(User.login == _login(tid)).first() is not None:
        return False
    row = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.telegram_user_id == tid).one_or_none()
    if row is None:
        return True
    return str(row.is_active or "") == "true" and not str(row.user_id or "").strip()


def ask(db, telegram_user_id: str, *, now: datetime) -> None:
    tid = str(telegram_user_id).strip()
    row = db.get(BotIntro, tid) or BotIntro(telegram_user_id=tid)
    row.asked_at = now
    db.add(row)
    db.flush()


def intro_state(db, telegram_user_id: str, *, now: datetime) -> str | None:
    row = db.get(BotIntro, str(telegram_user_id).strip())
    if row is None:
        return None
    return "fresh" if now - row.asked_at <= INTRO_TTL else "stale"


def cast_matches(db, name: str) -> list[dict]:
    """Где в книгах стоит это имя: роли каста и рассказчик. `[{"book_id", "book", "role"}]`.

    Права диктора в системе идут по имени («Мои роли», назначения, письма о касте), поэтому
    владелец должен знать, чьи роли получил назвавшийся (решение владельца 01.10: пускать
    сразу и писать ему)."""
    from app.models import BookBudget, Character
    from app.v2.cast_ops import parse_actor_name

    titles = {book.id: str(book.title or "") for book in db.query(ScriptBook).all()}
    found = []
    for ch in db.query(Character).filter(Character.actor_name != "").all():
        actor, _ = parse_actor_name(str(ch.actor_name or ""))
        if actor and names_match(actor, name):
            found.append({"book_id": ch.book_id, "book": titles.get(ch.book_id, ""), "role": str(ch.name or "")})
    for budget in db.query(BookBudget).filter(BookBudget.narrator_actor_name != "").all():
        actor, _ = parse_actor_name(str(budget.narrator_actor_name or ""))
        if actor and names_match(actor, name):
            found.append({"book_id": budget.book_id, "book": titles.get(budget.book_id, ""), "role": "Рассказчик"})
    return found


def create_dictor(db, telegram_user_id: str, name: str, *, now: datetime) -> dict:
    """Учётка диктора как у импорта голосов. `twins` — живые учётки с похожим именем,
    `cast` — роли и рассказчик в книгах, совпавшие по имени (см. `cast_matches`)."""
    tid = str(telegram_user_id).strip()
    twins = sorted({
        str(user.display_name or "")
        for user in db.query(User).filter(User.is_active == "true").all()
        if names_match(str(user.display_name or ""), name)
    })
    cast = cast_matches(db, name)
    made = create_user(db, display_name=name, roles=["dictor"], login=_login(tid),
                       password_hash=TELEGRAM_PASSWORD_HASH)
    user_id = made["user"]["id"]
    attach_telegram(db, user_id=user_id, telegram_user_id=tid)
    intro = db.get(BotIntro, tid)
    if intro is not None:
        db.delete(intro)
    db.flush()
    return {"user_id": user_id, "twins": twins, "cast": cast}


def enqueue_intro_followup(telegram_user_id: str) -> str | None:
    """Пересборка назначений — в воркер: долгая работа не для webhook."""
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    tid = str(telegram_user_id).strip()
    return enqueue_tracked_task(
        "high", "app.worker_tasks.perform_bot_intro_task", SessionLocal, BackgroundRun,
        job_kind="bot_intro", entity_type="telegram", entity_id=tid, run_key=f"bot_intro:{tid}",
        telegram_user_id=tid,
    )


def intro_followup(db, telegram_user_id: str) -> dict:
    """После знакомства: пересобрать назначения в книгах, где имя уже стоит в касте."""
    from app.services.casting import rebuild_assignments

    tid = str(telegram_user_id).strip()
    user = db.query(User).filter(User.login == _login(tid)).first()
    # Только книги, где имя стоит в касте или рассказчиком: остальные от новой учётки не меняются.
    books = sorted({m["book_id"] for m in cast_matches(db, str(user.display_name or ""))}) if user is not None else []
    rows = sum(rebuild_assignments(db, book_id) for book_id in books)
    return {"assignments": rows}
