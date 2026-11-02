"""Демо дикторов, пересланные владельцем боту (проект Б, спека 2026-10-22-forwarded-demos).

Граббер на Telethon входил в личный аккаунт владельца — риск бана. Теперь владелец пересылает
боту сообщение диктора с демо: в пересылке Telegram отдаёт `forward_origin`, и этого хватает,
чтобы узнать отправителя без входа в чужой аккаунт. Пересылка — фильтр владельца: «переслал —
значит, хочу этого диктора в базе голосов».
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.config import settings
from app.constants import DICTOR_ROLES
from app.models import BotDemoIntake, BotDemoItem, DictorLink, TelegramAuthAccount, User, UserRole
from app.services.bot_out import Outgoing, edit_off, send
from app.services.telegram import max_download_bytes, size_text
from app.services.user_admin import TELEGRAM_LOGIN_PREFIX
from app.services.demo_audio import MEDIA_EXTENSIONS

DM_PREFIX = "dm:"
#: Простой текст считается именем только для свежего вопроса: срока у приёма нет, и забытый
#: вопрос иначе принял бы любую фразу владельца («Завтра запись отменяется») за нового диктора.
#: Старше — только реплаем на вопрос.
PLAIN_TEXT_WINDOW = timedelta(hours=1)


@dataclass
class Forward:
    sender_key: str
    telegram_user_id: str
    tg_name: str
    tg_username: str
    kind: str  # user | hidden | chat
    files: list[dict] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    text: str = ""
    message_id: int = 0


def _origin(origin: dict) -> tuple[str, str, str, str, str]:
    kind = str(origin.get("type") or "")
    if kind == "user":
        sender = origin.get("sender_user") or {}
        tid = str(sender.get("id") or "")
        name = " ".join(str(sender.get(part) or "").strip() for part in ("first_name", "last_name")).strip()
        return f"tg:{tid}", tid, name, str(sender.get("username") or ""), "user"
    if kind == "hidden_user":
        name = " ".join(str(origin.get("sender_user_name") or "").split())
        return f"hidden:{name}"[:160], "", name, "", "hidden"
    return "", "", "", "", "chat"  # канал или группа — не диктор


def _is_media(name: str, mime: str) -> bool:
    return mime.startswith(("audio/", "video/")) or os.path.splitext(name)[1].lower() in MEDIA_EXTENSIONS


def _files(message: dict) -> list[dict]:
    """Своя выборка: `bot_uploads.incoming_file` видео считает «не аудио», а у дикторов бывают
    видеодемо и WAV, присланный документом без MIME."""
    found = []
    for key, fallback in (("audio", "audio.mp3"), ("voice", "voice.ogg"), ("video", "video.mp4"),
                          ("video_note", "video_note.mp4"), ("document", "")):
        f = message.get(key)
        if not isinstance(f, dict):
            continue
        name, mime = str(f.get("file_name") or fallback), str(f.get("mime_type") or "")
        if key == "document" and not _is_media(name, mime):
            continue
        found.append({"file_id": str(f.get("file_id") or ""), "file_name": (name or "демо")[:255],
                      "mime_type": mime[:80], "file_size": int(f.get("file_size") or 0)})
    return found


def _links_and_text(text: str, entities: list) -> tuple[list[str], str]:
    """Голые ссылки вырезаются из текста заметки. Смещения сущностей Telegram — в единицах
    UTF-16, а не в символах Python: эмодзи перед ссылкой иначе сдвигает срез."""
    units = text.encode("utf-16-le")
    links, spans = [], []
    for entity in entities or []:
        kind = entity.get("type")
        start, length = int(entity.get("offset") or 0) * 2, int(entity.get("length") or 0) * 2
        piece = units[start:start + length].decode("utf-16-le", errors="ignore")
        if kind == "url":
            links.append(piece if "://" in piece else f"https://{piece}")
            spans.append((start, start + length))
        elif kind == "text_link" and entity.get("url"):
            links.append(str(entity["url"]))  # слово под ссылкой — часть текста диктора, оно остаётся
    rest = bytearray()
    cursor = 0
    for start, end in sorted(spans):
        rest += units[cursor:start] + " ".encode("utf-16-le")
        cursor = max(cursor, end)
    rest += units[cursor:]
    return list(dict.fromkeys(links)), re.sub(r"\s+", " ", rest.decode("utf-16-le", errors="ignore")).strip()


def parse_forward(message: dict) -> Forward | None:
    origin = message.get("forward_origin")
    if not isinstance(origin, dict):
        return None
    sender_key, tid, name, username, kind = _origin(origin)
    text = str(message.get("text") or message.get("caption") or "")
    entities = message.get("entities") or message.get("caption_entities") or []
    links, rest = _links_and_text(text, entities)
    return Forward(sender_key=sender_key, telegram_user_id=tid, tg_name=name[:120], tg_username=username[:64],
                   kind=kind, files=_files(message), links=links, text=rest,
                   message_id=int(message.get("message_id") or 0))


# ── приём в диалоге ────────────────────────────────────────────────────────────────────────


def is_owner(tid: str) -> bool:
    """Пересылку принимает только владелец: он — фильтр, кого брать в базу голосов (решение 8)."""
    owner = str(settings.owner_telegram_id or "").strip()
    return bool(owner) and str(tid) == owner


def known_dictor(db, telegram_user_id: str) -> tuple[User | None, str]:
    """(диктор, "") — узнан; (None, пометка) — id у чужой учётки, её не трогаем; (None, "") — новый.

    Ищем как `bot_dialog._person`: у импортированных, но не входивших дикторов
    `TelegramAuthAccount.user_id` пуст, и находятся они только по логину `tg_<id>`."""
    tid = str(telegram_user_id or "").strip()
    if not tid:
        return None, ""
    row = db.query(TelegramAuthAccount).filter(TelegramAuthAccount.telegram_user_id == tid).first()
    linked = str(getattr(row, "user_id", "") or "").strip()
    user = db.get(User, linked) if linked else None
    if user is None:
        user = db.query(User).filter(User.login == f"{TELEGRAM_LOGIN_PREFIX}{tid}").first()
    if user is None and linked:
        # строка доступа ссылается на удалённую учётку: `attach_telegram` откажет (telegram_taken)
        return None, "удалённой учётки"
    if user is None:
        # строку доступа выключили нарочно (как читает её `bot_intro.may_introduce`) — не оживляем
        return (None, "доступ по этому Telegram выключен") if row is not None and row.is_active != "true" else (None, "")
    name = str(user.display_name or user.login or "")
    if user.is_active != "true":
        return None, f"{name} (выключена)"
    roles = {r.role for r in db.query(UserRole).filter(UserRole.user_id == user.id)}
    if not roles & DICTOR_ROLES:
        return None, f"{name} (не диктор)"
    return user, ""


def _tag(intake: BotDemoIntake) -> str:
    return f"#{intake.id[:6]}"


def _similar(db, name: str, *, limit: int = 3) -> list[User]:
    """Похожие активные дикторы — против двойников: карточку выбирают кнопкой, а не заводят заново."""
    from app.services.dictors import _fold, _same_person

    if not name:
        return []
    surname = {_fold(word) for word in name.split()}
    found = []
    for user in (db.query(User).join(UserRole, UserRole.user_id == User.id)
                 .filter(User.is_active == "true", UserRole.role.in_(sorted(DICTOR_ROLES)))
                 .order_by(User.display_name).all()):
        display = str(user.display_name or "")
        first = _fold(display.split()[0]) if display.split() else ""
        if user not in found and (_same_person(display, name) or (first and first in surname)):
            found.append(user)
    return found[:limit]


def _ask(chat_id, intake: BotDemoIntake, db) -> Outgoing:
    from app.services.bot_intro import clean_name

    who = intake.tg_name or "без имени"
    if intake.tg_username:
        who += f" (@{intake.tg_username})"
    text = f"Новый диктор: {who}. Как записать? Напишите фамилию и имя или выберите. {_tag(intake)}"
    if intake.foreign_note:
        text += f"\nЭтот Telegram уже у учётки {intake.foreign_note} — новую карточку заведу без него."
    rows = []
    suggested = clean_name(intake.tg_name)
    if suggested:
        rows.append([{"text": suggested, "callback_data": f"{DM_PREFIX}n:{intake.id}"}])
    for user in _similar(db, intake.tg_name):
        rows.append([{"text": f"Это {user.display_name}"[:60], "callback_data": f"{DM_PREFIX}p:{intake.id}:{user.id}"}])
    rows.append([{"text": "Отмена", "callback_data": f"{DM_PREFIX}x:{intake.id}"}])
    return send(chat_id, text, {"inline_keyboard": rows})


def _ask_namesake(chat_id, intake: BotDemoIntake, db) -> Outgoing:
    name = intake.pending_name
    rows = [[{"text": f"Это {user.display_name}"[:60], "callback_data": f"{DM_PREFIX}p:{intake.id}:{user.id}"}]
            for user in _similar(db, name)]
    rows.append([{"text": "Это другой человек — завести", "callback_data": f"{DM_PREFIX}o:{intake.id}"}])
    rows.append([{"text": "Отмена", "callback_data": f"{DM_PREFIX}x:{intake.id}"}])
    return send(chat_id, f"Имя «{name}» уже занято. Это кто-то из них или другой человек? {_tag(intake)}",
                {"inline_keyboard": rows})


def _add_items(db, intake: BotDemoIntake, fwd: Forward, now) -> list[BotDemoItem]:
    """Позиции пересылки. Повтор вебхука несёт тот же номер сообщения — такие пропускаются."""
    wanted = [("file", f["file_id"], f) for f in fwd.files] + [("link", url[:1024], {}) for url in fwd.links]
    if fwd.text:
        wanted.append(("text", "", {}))
    added = []
    for kind, ref, f in wanted:
        if db.query(BotDemoItem.id).filter_by(source_message_id=fwd.message_id, kind=kind, ref=ref).first():
            continue
        item = BotDemoItem(intake_id=intake.id, source_message_id=fwd.message_id, kind=kind, ref=ref,
                           file_name=f.get("file_name", ""), mime_type=f.get("mime_type", ""),
                           file_size=int(f.get("file_size") or 0), text=fwd.text if kind == "text" else "",
                           state="waiting", created_at=now, updated_at=now)
        db.add(item)
        added.append(item)
    db.flush()
    return added


def append_note(db, user_id: str, text: str, now) -> None:
    """Дописать, а не перезаписать: в заметке может быть то, что владелец писал руками. Предел
    режет хвост нового текста, а не старую заметку."""
    from app.services.dictors import NOTE_MAX, _profile

    profile = _profile(db, user_id)
    old = str(profile.note or "")
    line = f"{now:%d.%m} из Telegram: {text}"
    room = NOTE_MAX - len(old) - (1 if old else 0)
    if room > 0:
        profile.note = (old + "\n" if old else "") + line[:room]


def _settle(db, intake: BotDemoIntake, user: User, now) -> tuple[list[Outgoing], dict]:
    """Позиции решённого приёма — в работу: файлы и ссылки в воркер, портфолио и закрытые
    ссылки сразу в карточку (качать там нечего), текст — в заметку."""
    from app.services.demo_fetch import classify

    counts = {"file": 0, "link": 0, "saved": 0, "text": 0}
    jobs = []
    items = (db.query(BotDemoItem).filter_by(intake_id=intake.id, state="waiting")
             .order_by(BotDemoItem.source_message_id, BotDemoItem.created_at).all())
    for item in items:
        item.updated_at = now
        if item.kind == "text":
            append_note(db, user.id, item.text, now)
            item.state, counts["text"] = "stored", counts["text"] + 1
        elif item.kind == "link" and classify(item.ref) in ("portfolio", "private"):
            if not db.query(DictorLink.id).filter_by(user_id=user.id, url=item.ref).first():
                db.add(DictorLink(user_id=user.id, url=item.ref, title=""))
            item.state, counts["saved"] = "link_saved", counts["saved"] + 1
        else:
            item.state = "queued"
            counts[item.kind] += 1
            jobs.append(Outgoing("enqueue_bot_demo", {"id": item.id}))
    db.flush()
    return jobs, counts


def _summary(name: str, counts: dict) -> str:
    parts = []
    if counts["file"]:
        parts.append(f"файлов: {counts['file']}")
    if counts["link"]:
        parts.append(f"ссылок скачать: {counts['link']}")
    if counts["saved"]:
        parts.append(f"ссылок в карточку: {counts['saved']}")
    if counts["text"]:
        parts.append("текст — в заметку")
    tail = " Сохраню и напишу." if counts["file"] or counts["link"] else ""
    return f"{name}: принято — {', '.join(parts) or 'ничего нового'}.{tail}"


def receive_forward(db, *, chat_id, message: dict, now) -> list[Outgoing]:
    fwd = parse_forward(message)
    if fwd is None:
        return []
    if fwd.kind == "chat" or not fwd.sender_key:
        return [send(chat_id, "Пересланное не от человека — демо не добавлено.")]
    if not (fwd.files or fwd.links or fwd.text):
        return [send(chat_id, "Здесь нет демо — пришлите файл или ссылку.")]
    user, foreign = known_dictor(db, fwd.telegram_user_id)
    if user is not None:
        intake = (db.query(BotDemoIntake).filter_by(sender_key=fwd.sender_key, state="resolved", user_id=user.id)
                  .order_by(BotDemoIntake.created_at.desc()).first())
        if intake is None:
            intake = BotDemoIntake(sender_key=fwd.sender_key, telegram_user_id=fwd.telegram_user_id,
                                   tg_name=fwd.tg_name, tg_username=fwd.tg_username, state="resolved",
                                   user_id=user.id, created_at=now, updated_at=now)
            db.add(intake)
            db.flush()
        if not _add_items(db, intake, fwd, now):
            return []  # повтор того же сообщения
        jobs, counts = _settle(db, intake, user, now)
        return [send(chat_id, _summary(str(user.display_name or ""), counts)), *jobs]
    open_intake = db.query(BotDemoIntake).filter_by(sender_key=fwd.sender_key, state="ask").first()
    if open_intake is not None:
        if _add_items(db, open_intake, fwd, now):
            open_intake.updated_at = now  # досылка в пачку держит вопрос свежим для простого текста
        return []  # вопрос уже задан — пачка ждёт ответа молча (решение 9)
    intake = BotDemoIntake(sender_key=fwd.sender_key, telegram_user_id=fwd.telegram_user_id, tg_name=fwd.tg_name,
                           tg_username=fwd.tg_username, state="ask", foreign_note=foreign[:200],
                           created_at=now, updated_at=now)
    db.add(intake)
    db.flush()
    if not _add_items(db, intake, fwd, now):
        # повтор уже разобранного сообщения (приём решён или отменён) — второй вопрос завёл
        # бы пустую учётку-двойник
        db.delete(intake)
        db.flush()
        return []
    return [_ask(chat_id, intake, db)]


def _claim(db, intake_id: str, now) -> bool:
    """Захват одной записью, как в `bot_uploads.press`: два нажатия подряд — одна учётка."""
    return db.query(BotDemoIntake).filter(BotDemoIntake.id == intake_id, BotDemoIntake.state == "ask").update(
        {"state": "resolved", "updated_at": now}, synchronize_session=False) == 1


def _attach_note(db, intake: BotDemoIntake, user: User) -> str:
    """Telegram id к выбранной карточке — только этим нажатием владельца и только если он свободен."""
    from app.services.user_admin import UserAdminError, attach_telegram

    tid = intake.telegram_user_id
    if not tid:
        return ""
    if intake.foreign_note:
        return f"Telegram не привязан: он у учётки {intake.foreign_note}."
    if db.query(TelegramAuthAccount.telegram_user_id).filter(TelegramAuthAccount.user_id == user.id).first():
        return f"Telegram не привязан: у {user.display_name} уже есть свой."
    try:
        attach_telegram(db, user_id=user.id, telegram_user_id=tid)
    except UserAdminError as exc:
        return f"Telegram не привязан: {exc.args[0] if exc.args else exc}."
    return ""


def _resolve(db, chat_id, intake: BotDemoIntake, now, *, name: str = "", user_id: str = "",
             namesake: bool = False) -> list[Outgoing]:
    from app.services.dictors import DictorError, create_dictor

    if not _claim(db, intake.id, now):
        return [send(chat_id, "Этот вопрос уже закрыт.")]
    db.refresh(intake)
    note = ""
    if user_id:
        user = db.get(User, user_id)
        if user is None or known_dictor_user(db, user) is None:
            intake.state = "ask"
            return [send(chat_id, "Эта карточка недоступна — выберите другую или напишите имя.")]
        note = _attach_note(db, intake, user)
        head = f"Добавляю в карточку: {user.display_name}."
    else:
        tid = "" if intake.foreign_note else intake.telegram_user_id
        try:
            # Точка сохранения: `create_dictor` заводит учётку до привязки Telegram, и отказ
            # привязки иначе оставил бы полузаведённую `tg_<id>`, а вебхук её зафиксировал.
            with db.begin_nested():
                made = create_dictor(db, name, telegram_user_id=tid, username=intake.tg_username, namesake=namesake)
        except DictorError as exc:
            intake.state = "ask"
            if exc.code == "name_taken":
                intake.pending_name = name[:120]
                db.flush()
                return [_ask_namesake(chat_id, intake, db)]
            db.flush()
            return [send(chat_id, f"Не получилось завести диктора ({exc.code}) — напишите другое имя. {_tag(intake)}")]
        user = db.get(User, made["user_id"])
        head = f"Завёл диктора {user.display_name}."
    intake.user_id, intake.updated_at = user.id, now
    jobs, counts = _settle(db, intake, user, now)
    lines = [head, _summary(str(user.display_name or ""), counts)] + ([note] if note else [])
    return [send(chat_id, "\n".join(lines)), *jobs]


def known_dictor_user(db, user: User) -> User | None:
    roles = {r.role for r in db.query(UserRole).filter(UserRole.user_id == user.id)}
    return user if user.is_active == "true" and roles & DICTOR_ROLES else None


def press(db, *, chat_id, data: str, now, message_id=None) -> list[Outgoing]:
    from app.services.bot_intro import clean_name

    action, intake_id, extra = (data[len(DM_PREFIX):].split(":") + ["", ""])[:3]
    intake = db.get(BotDemoIntake, intake_id)
    if intake is None:
        return []
    out = [edit_off(chat_id, message_id)] if message_id is not None and intake.state == "ask" else []
    if action == "x":
        if intake.state != "ask":
            return [send(chat_id, "Этот вопрос уже закрыт.")]
        intake.state, intake.updated_at = "cancelled", now
        db.query(BotDemoItem).filter_by(intake_id=intake.id, state="waiting").update(
            {"state": "cancelled", "updated_at": now}, synchronize_session=False)
        db.flush()
        return out + [send(chat_id, "Отменено.")]
    if action == "n":
        name = clean_name(intake.tg_name)
        return out + (_resolve(db, chat_id, intake, now, name=name) if name else [])
    if action == "p":
        return out + _resolve(db, chat_id, intake, now, user_id=extra)
    if action == "o" and intake.pending_name:
        return out + _resolve(db, chat_id, intake, now, name=intake.pending_name, namesake=True)
    return []


def take_name(db, *, chat_id, text: str, reply_to: dict | None, now, reply_only: bool = False,
              other_since: datetime | None = None) -> list[Outgoing] | None:
    """Имя, написанное текстом. Реплай на вопрос — всегда в свой приём. Простой текст — в
    единственный открытый приём, если он свежее другого ожидания (`other_since` — рассылки);
    при нескольких открытых — подсказка ответить реплаем, иначе демо легли бы в чужую карточку.
    `None` — текст не приёму, пусть разбирает остальной диалог."""
    from app.services.bot_intro import clean_name

    opened = db.query(BotDemoIntake).filter_by(state="ask").order_by(BotDemoIntake.created_at).all()
    if not opened:
        return None
    intake = None
    match = re.search(r"#([0-9a-f]{6})\b", str((reply_to or {}).get("text") or ""))
    if match:
        intake = next((row for row in opened if row.id.startswith(match.group(1))), None)
        if intake is None and db.query(BotDemoIntake.id).filter(BotDemoIntake.id.like(f"{match.group(1)}%")).first():
            # реплай на закрытый вопрос — не отдавать имя другому открытому приёму (ревью I-1)
            return [send(chat_id, "Этот вопрос уже закрыт.")]
    if intake is None:
        if reply_only:
            return None
        fresh = [row for row in opened if now - row.updated_at <= PLAIN_TEXT_WINDOW]
        if not fresh:
            return None
        if other_since is not None and other_since > max(row.updated_at for row in fresh):
            return None
        if len(fresh) > 1:
            return [send(chat_id, f"Ответьте на вопрос о нужном дикторе — открыто {len(fresh)}.")]
        intake = fresh[0]
    name = clean_name(text)
    if not name:
        return [send(chat_id, f"Напишите фамилию и имя — от двух до четырёх слов. {_tag(intake)}")]
    return _resolve(db, chat_id, intake, now, name=name)


# ── сохранение в воркере ───────────────────────────────────────────────────────────────────


def enqueue_store_demo(item_id: str) -> str | None:
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    return enqueue_tracked_task(
        "high", "app.worker_tasks.perform_bot_demo_task", SessionLocal, BackgroundRun,
        job_kind="bot_demo", entity_type="bot_demo", entity_id=str(item_id),
        run_key=f"bot_demo:{item_id}", item_id=str(item_id),
    )


def _cards_link() -> str:
    return f"{settings.app_base_url.rstrip('/')}/app/dictors"


def _title(item: BotDemoItem, path: str, name: str) -> str:
    from app.services.demo_audio import demo_title

    if item.kind == "file" and item.file_name == "voice.ogg":
        return "голосовое"
    return demo_title(item.file_name if item.kind == "file" else os.path.basename(path), name)


def _save_file(db, item: BotDemoItem, user: User, fetch, workdir: str) -> list[str]:
    """Поток Telegram — во временный файл: ffmpeg нужен путь. На своём узле это до 2 ГБ, поэтому
    копируем кусками, а не читаем в память."""
    from app.services.dictors import DictorError, add_demo

    if item.file_size > max_download_bytes():
        raise _TooBig()
    path = os.path.join(workdir, os.path.basename(item.file_name) or "demo.bin")
    with fetch(item.ref) as source, open(path, "wb") as target:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            target.write(chunk)
    try:
        return [add_demo(db, user.id, path, _title(item, path, user.display_name), source="telegram")["title"]]
    except DictorError as exc:
        if exc.code == "duplicate":
            return []
        raise


def _save_link(db, item: BotDemoItem, user: User, download, workdir: str) -> tuple[list[str], int]:
    """(названия сохранённых, сколько дублей). Ничего годного — `FetchError`: ссылка ляжет в карточку."""
    from app.services.dictors import DictorError, add_demo
    from app.services.demo_fetch import FetchError
    from app.services.demo_audio import ConvertError, is_media_file

    saved, dups = [], 0
    for path in download(item.ref, workdir):
        if not is_media_file(path) and not path.endswith(".bin"):
            continue
        try:
            saved.append(add_demo(db, user.id, path, _title(item, path, user.display_name), source="telegram")["title"])
        except DictorError as exc:
            if exc.code != "duplicate":
                raise
            dups += 1
        except ConvertError:
            # Google Drive по закрытой ссылке отдаёт HTML под именем «демо.bin»; битый файл в
            # папке не должен откатывать уже сохранённые — он просто «не годен» (ревью I-3)
            continue
    if not saved and not dups:
        raise FetchError("no_media")
    return saved, dups


class _TooBig(Exception):
    pass


def store_item(db, item_id: str, *, fetch, download, send_text, workdir: str | None = None,
               now: datetime | None = None) -> str:
    """Задача воркера: одна позиция пересылки → демо в карточке. Итог — владельцу одной строкой:
    пересылка не должна пропасть молча."""
    import logging
    import tempfile

    from app.services.demo_fetch import FetchError
    from app.time_utils import utcnow_naive

    now = now or utcnow_naive()
    item = db.get(BotDemoItem, str(item_id or ""))
    if item is None or item.state != "queued":
        return "skip"
    intake = db.get(BotDemoIntake, item.intake_id)
    user = db.get(User, intake.user_id) if intake is not None and intake.user_id else None
    owner = str(settings.owner_telegram_id)
    name = str(getattr(user, "display_name", "") or "диктор")
    state, text = "failed", f"Не получилось сохранить демо {name} — пришлите ещё раз."
    try:
        if user is None:
            raise LookupError("no_user")
        with tempfile.TemporaryDirectory(dir=workdir) as tmp:
            if item.kind == "file":
                titles = _save_file(db, item, user, fetch, tmp)
                state = "stored" if titles else "duplicate"
                text = (f"{name}: демо «{titles[0]}» добавлено — {_cards_link()}" if titles
                        else f"Это демо у {name} уже есть.")
            else:
                try:
                    titles, dups = _save_link(db, item, user, download, tmp)
                except FetchError:
                    db.rollback()
                    if not db.query(DictorLink.id).filter_by(user_id=user.id, url=item.ref).first():
                        db.add(DictorLink(user_id=user.id, url=item.ref, title=""))
                    state, text = "link_saved", f"Не скачалось: {item.ref} — ссылку сохранил в карточке {name}."
                else:
                    state = "stored" if titles else "duplicate"
                    text = (f"{name}: по ссылке добавлено демо — {len(titles)}"
                            + (f", уже были — {dups}" if dups else "") + f". {_cards_link()}"
                            if titles else f"Демо по ссылке у {name} уже есть.")
    except _TooBig:
        db.rollback()
        text = f"Файл больше {size_text(max_download_bytes())} — демо {name} не добавлено."
    except Exception as exc:  # noqa: BLE001 — строка не должна висеть «queued», а владелец — в неведении
        db.rollback()
        logging.getLogger(__name__).warning("Демо из бота %s не сохранено: %s", item_id, type(exc).__name__)
    item = db.get(BotDemoItem, str(item_id))
    item.state, item.result, item.updated_at = state, text[:255], now
    db.commit()
    if send_text is not None:
        send_text(owner, text)
    return state
