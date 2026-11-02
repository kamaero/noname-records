"""Проба через бот (пакет C2, спека 2026-10-05-bot-auditions, раздел 4).

Диктор присылает файл или голосовое — бот берёт его открытые приглашения (сроки пробы, адресат
по правилу писем) и спрашивает роль. Проба — только по приглашению, как в порядке работы `/help`:
без приглашения файл не принимаем. Скачивание и запись — в воркере: файл до 2 ГБ (свой узел Bot API) не должен
держать единственный uvicorn.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.config import settings
from app.models import BotUpload, Character, RoleDeadline, ScriptBook
from app.services import studio
from app.services.audition_card import send_card
from app.services.bot_out import Outgoing, send
from app.services.role_deadlines import open_deadline, same_person
from app.services.telegram import max_download_bytes, size_text

BU_PREFIX = "bu:"
#: предел скачивания файла ботом (Bot API `getFile`)
CHOICE_TTL = timedelta(hours=24)
ONLY_INVITED = ("Проба — только по приглашению. Когда понадобится ваш голос, бот пришлёт приглашение: "
                "роль, книгу и срок.")


def _site_link() -> str:
    return f"{settings.app_base_url.rstrip('/')}/app/recording"


def incoming_file(message: dict) -> dict | None:
    """Вложение сообщения: поля файла, `{"bad": True}` — не аудио, `None` — вложения нет."""
    if isinstance(message.get("voice"), dict):
        f, kind, name = message["voice"], "voice", "voice.ogg"
    elif isinstance(message.get("audio"), dict):
        f, kind = message["audio"], "audio"
        name = str(f.get("file_name") or "audio.mp3")
    elif isinstance(message.get("document"), dict) and str(message["document"].get("mime_type") or "").startswith("audio/"):
        f, kind = message["document"], "document"
        name = str(f.get("file_name") or "audio")
    elif any(key in message for key in ("video", "video_note", "photo", "document", "animation", "sticker")):
        return {"bad": True}
    else:
        return None
    return {"file_id": str(f.get("file_id") or ""), "file_unique_id": str(f.get("file_unique_id") or ""),
            "kind": kind, "mime_type": str(f.get("mime_type") or ""), "file_name": name[:255],
            "file_size": int(f.get("file_size") or 0), "duration": int(f.get("duration") or 0)}


def _invitations(db, tid: str, user) -> list[tuple[Character, ScriptBook]]:
    from app.services.bot_status import owner_by_chat

    owns = owner_by_chat(db, tid, user)
    found = []
    for row in db.query(RoleDeadline).filter(RoleDeadline.closed_at.is_(None), RoleDeadline.kind == "audition")\
            .order_by(RoleDeadline.due_at.asc()).all():
        if not owns(row.actor_name):
            continue
        ch, book = db.get(Character, row.character_id), db.get(ScriptBook, row.book_id)
        # Приглашение на пробу в озвученную книгу не действует: срок закрывается при
        # озвучивании, но этот отбор не должен зависеть от того, успел ли он закрыться.
        if ch is not None and book is not None and book.voiced_at is None:
            found.append((ch, book))
    return found


def receive(db, *, tid: str, chat_id, user, info: dict, now: datetime) -> list[Outgoing] | None:
    """Ответы на вложение; `None` — это не проба, пусть ответит обычный диалог (фото или
    стикер от человека без приглашения не должен слышать «пришлите аудио» — ревью C2)."""
    invited = _invitations(db, tid, user)
    if not invited:
        return None if info.get("bad") else [send(chat_id, ONLY_INVITED)]
    if info.get("bad"):
        return [send(chat_id, "Пришлите пробу аудиофайлом или голосовым.")]
    limit = max_download_bytes()
    if info["file_size"] > limit:
        return [send(chat_id, f"Файл больше {size_text(limit)} — загрузите пробу на сайте, "
                              f"раздел «Загрузка»: {_site_link()}")]
    row = BotUpload(telegram_user_id=str(tid), created_at=now, updated_at=now,
                    **{k: info[k] for k in ("file_id", "file_unique_id", "kind", "mime_type", "file_name",
                                            "file_size", "duration")})
    db.add(row)
    db.flush()
    cancel = [{"text": "Отмена", "callback_data": f"{BU_PREFIX}x:{row.id}"}]
    if len(invited) == 1:
        ch, book = invited[0]
        buttons = {"inline_keyboard": [[{"text": "Да", "callback_data": f"{BU_PREFIX}c:{row.id}:{ch.id}"}, *cancel]]}
        return [send(chat_id, f"Проба на «{ch.name}» («{book.title}»)?", buttons)]
    rows = [[{"text": f"«{ch.name}» («{book.title}»)"[:60], "callback_data": f"{BU_PREFIX}c:{row.id}:{ch.id}"}]
            for ch, book in invited]
    return [send(chat_id, "На какую роль эта проба?", {"inline_keyboard": [*rows, cancel]})]


def press(db, *, tid: str, chat_id, data: str, user, now: datetime) -> list[Outgoing]:
    from app.services.bot_status import owner_by_chat

    action, upload_id, character_id = (data[len(BU_PREFIX):].split(":") + ["", ""])[:3]
    row = db.get(BotUpload, upload_id)
    if row is None or row.telegram_user_id != str(tid):
        return []  # чужое нажатие молча гасим
    if action == "x":
        if row.state == "choose":
            row.state, row.updated_at = "cancelled", now
            db.flush()
        if row.state == "cancelled":
            return [send(chat_id, "Отменено.")]
        # после «Да» файл уже в работе — честно, а не «Отменено» при уходящей карточке (ревью C2)
        return [send(chat_id, f"Проба уже принята — отменить её в боте нельзя. Если нужно, {studio.write_to()}.")]
    if action != "c" or row.state != "choose":
        return [send(chat_id, "Эта проба уже принята или отменена.")]
    if row.created_at < now - CHOICE_TTL:
        return [send(chat_id, "Выбор устарел — пришлите файл ещё раз.")]
    deadline = open_deadline(db, character_id)
    if deadline is None or deadline.kind != "audition" or not owner_by_chat(db, tid, user)(deadline.actor_name):
        return [send(chat_id, "Приглашение уже неактуально.")]
    # Захват одной записью, как у рассылки: два «Да» подряд — одна задача. Назначение ставим
    # явно: строку мог завести приём дублей с вопросом «дубль или проба?» (bot_takes).
    claimed = (db.query(BotUpload)
               .filter(BotUpload.id == row.id, BotUpload.state == "choose")
               .update({"state": "queued", "character_id": character_id, "actor_name": deadline.actor_name,
                        "purpose": "audition", "updated_at": now}, synchronize_session=False))
    if claimed != 1:
        return []
    db.expire(row)
    # очередь общая с конвейером — ответ может прийти не сразу; без обещания диктор пришлёт файл ещё раз
    return [send(chat_id, "Сохраняю пробу — пришлю подтверждение, когда она ляжет (обычно в течение "
                          "нескольких минут)."), Outgoing("enqueue_bot_upload", {"id": row.id})]


def enqueue_store(upload_id: str) -> str | None:
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    return enqueue_tracked_task(
        "high", "app.worker_tasks.perform_bot_upload_task", SessionLocal, BackgroundRun,
        job_kind="bot_upload", entity_type="bot_upload", entity_id=str(upload_id),
        run_key=f"bot_upload:{upload_id}", upload_id=str(upload_id),
    )


def store(db, upload_id: str, *, fetch, send_text, send_audio, store_file=None,
          now: datetime | None = None) -> str:
    """Задача воркера: скачать, положить в хранилище тем же путём, что сайт, сказать диктору,
    отправить карточку. Имя актёра — из каста (без «?»): по нему лист проб, оценки и отказы
    находят пару, как на сайте, где диктор выбирает себя из каста."""
    import json
    import logging

    from app.models import AuditLog
    from app.services.audio_uploads import AUDITION, derive_book_code, store_audio_file
    from app.services.shared_runtime import safe_name
    from app.time_utils import utcnow_naive

    log = logging.getLogger(__name__)
    now = now or utcnow_naive()
    moved = False
    row = db.get(BotUpload, str(upload_id or ""))
    if row is None or row.state != "queued":
        return "skip"
    tid = row.telegram_user_id
    try:
        ch = db.get(Character, row.character_id)
        book = db.get(ScriptBook, ch.book_id) if ch is not None else None
        # Имя — зафиксированное при «Да», и приглашение всё ещё его: пока задача ждала в
        # очереди, роль могли отдать другому — тогда файл не кладём (ревью C2).
        actor = str(row.actor_name or "")
        by_agent = row.purpose == "agent"  # агент грузит за подопечного без приглашения
        deadline = open_deadline(db, ch.id) if ch is not None and not by_agent else None
        invited = by_agent or (deadline is not None and deadline.kind == "audition"
                               and same_person(deadline.actor_name, actor))
        if ch is None or book is None or book.voiced_at is not None or not actor or not invited:
            moved = True
            raise LookupError("role_gone")
        save = store_file or (lambda db, **kw: store_audio_file(db, safe_name=safe_name, **kw))
        # Потоком: со своего узла Bot API приходят файлы до 2 ГБ, держать их в памяти нельзя.
        with fetch(row.file_id) as source:
            audio = save(db, source=source, mime_type=row.mime_type or "audio/mpeg",
                         original_filename=row.file_name, book_code=derive_book_code(str(book.title or "")),
                         chapter="", role=str(ch.name or ""), actor_name=actor, kind=AUDITION,
                         size_hint=int(row.file_size or 0))
        row.state, row.audio_id, row.updated_at = "stored", audio.id, now
        db.add(AuditLog(user_id="", entity_type="bot_upload", entity_id=row.id, action="bot_upload_stored",
                        created_at=now, payload_json=json.dumps({"audio_id": audio.id, "telegram_user_id": tid},
                                                                ensure_ascii=False)))
        db.commit()
    except Exception as exc:  # noqa: BLE001 — сбой не должен оставить строку «queued» и диктора в неведении
        db.rollback()
        log.warning("Проба из бота %s не сохранена: %s", upload_id, type(exc).__name__)
        row = db.get(BotUpload, str(upload_id))
        row.state, row.updated_at = "failed", now
        db.commit()
        if moved:
            send_text(tid, "Не получилось принять пробу: роль уже передана другому исполнителю или приглашение "
                           f"закрыто. Если это ошибка, {studio.write_to()}.")
        else:
            send_text(tid, f"Не получилось сохранить пробу — попробуйте ещё раз или загрузите на сайте: {_site_link()}")
        return "failed"
    if row.purpose == "agent":
        send_text(tid, f"Проба принята — «{ch.name}», {actor}.")
    else:
        send_text(tid, f"Проба принята — «{ch.name}». Ответ придёт сюда.")
    # Голосовое и играемое аудио — пересылкой по `file_id`; документ (WAV) Telegram в плеер не
    # отдаст — карточка возьмёт файл из хранилища и сделает MP3-копию.
    # .ogg, присланный как музыка, — тип «аудио»: sendVoice его отклонит (ревью C2)
    playable = row.kind == "voice" or (row.kind == "audio" and str(audio.codec or "").lower() in ("mp3", "aac"))
    send_card(db, audio.id, file_id=row.file_id if playable else "", send_audio=send_audio, send_text=send_text)
    return "stored"
