"""Дубль через бот (спека 2026-10-28-bot-takes, часть 3): учёт, выбор роли, очередь.

Диктор с утверждённой ролью в книге, которая сейчас записывается, присылает файл любого имени —
бот определяет роль, ставит задачу в воркер, а тот режет запись по главам и сохраняет. Книгу не
спрашиваем: у студии одна активная книга (решение владельца 28.10). Строка учёта — та же
`BotUpload`, что у проб, с `purpose="take"`: кнопке нужен короткий `id`, а не `file_id`.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models import AudioFile, BotUpload, Character, ScriptBook
from app.services import bot_uploads, studio, take_format
from app.services.audio_naming import book_token, latin_word
from app.services.bot_out import Outgoing, send
from app.services.take_split import transcribe_whole
from app.services.telegram import max_download_bytes, size_text

BT_PREFIX = "bt:"
QUEUED = "File received. I will transcribe it, split it into chapters and send a report in a few minutes."
NO_BOOK = f"The studio is not currently recording a book. If this is an error, {studio.write_to()}."


def my_roles(db, *, tid: str, user, book) -> list[Character]:
    """Утверждённые роли человека в книге. Роль с «?» — предварительная: дубль по ней
    класть рано, по ней ещё идут пробы."""
    from app.services.bot_status import owner_by_chat
    from app.v2.cast_ops import parse_actor_name

    if book is None:
        return []
    owns = owner_by_chat(db, tid, user)
    found = []
    for ch in db.query(Character).filter(Character.book_id == book.id).order_by(Character.name.asc()).all():
        name, tentative = parse_actor_name(ch.actor_name)
        if name and not tentative and owns(name):
            found.append(ch)
    return found


def _has_approved_role(db, *, tid: str, user) -> bool:
    """Есть ли у человека утверждённая роль хоть в одной книге — тот же разбор, что `my_roles`."""
    from app.services.bot_status import owner_by_chat
    from app.v2.cast_ops import parse_actor_name

    owns = owner_by_chat(db, tid, user)
    for (raw,) in db.query(Character.actor_name).filter(Character.actor_name != "").distinct():
        name, tentative = parse_actor_name(raw)
        if name and not tentative and owns(name):
            return True
    return False


def _role_keys(ch: Character) -> set[str]:
    """Роль латиницей и кириллицей, с псевдонимами: имя файла пишут транслитом
    (`Lymeli`), а сверка с кастом должна найти «Лымэли» — та же транслитерация, что даёт
    имя при сохранении (`latin_word`), иначе правильно названный файл не узнает сам бот."""
    from app.services.audition_rejections import role_keys
    from app.v2.cast_ops import split_aliases

    keys = set(role_keys(ch.name, ch.aliases or ""))
    keys |= {latin_word(item).lower() for item in [ch.name, *split_aliases(ch.aliases or "")] if str(item or "").strip()}
    keys.discard("")
    return keys


def _role_from_filename(filename: str, roles: list[Character], book: ScriptBook) -> Character | None:
    """Роль, которую называет имя файла, или `None`. Разбор — тот же, что на сайте; кроме
    роли целиком сверяем и отдельные слова: без фамилии диктора в имени разбор клеит
    роль с соседними словами («Lymeli Lomakin»)."""
    from app.services.audio_uploads import derive_book_code, parse_batch_audio_filename
    from app.services.author_profile import normalize_name
    from app.services.shared_runtime import normalize_role_label

    parsed = parse_batch_audio_filename(filename, default_book_code=derive_book_code(str(book.title or "")),
                                        default_actor_name="", normalize_role_label=normalize_role_label)
    token = str(parsed.get("role") or "")
    if not token:
        return None
    candidates = {latin_word(token).lower(), normalize_name(token)}
    candidates |= {latin_word(word).lower() for word in token.split()}
    candidates.discard("")
    hits = [ch for ch in roles if _role_keys(ch) & candidates]
    return hits[0] if len(hits) == 1 else None


def _claim(db, row_id: str, ch: Character, now: datetime) -> bool:
    """Захват одной записью, как у проб: два нажатия подряд — одна задача."""
    from app.v2.cast_ops import parse_actor_name

    actor, _ = parse_actor_name(ch.actor_name)
    claimed = (db.query(BotUpload)
               .filter(BotUpload.id == row_id, BotUpload.state == "choose", BotUpload.purpose == "take")
               .update({"state": "queued", "character_id": ch.id, "role_name": str(ch.name or "")[:120],
                        "actor_name": actor[:120], "updated_at": now}, synchronize_session=False))
    return claimed == 1


#: строка «в очереди»/«в работе» дольше этого — мёртвая (воркер упал, задачу потеряли):
#: она не мешает диктору прислать файл заново. Час записи распознаётся минуты, 15 ч — около
#: часа; шесть часов — с запасом.
STALE_AFTER = timedelta(hours=6)
IN_FLIGHT = ("queued", "processing")


def _taken_state(now: datetime):
    """Условие «файл уже принят»: разложен, или ещё живая задача в очереди или в работе."""
    from sqlalchemy import and_, or_

    return or_(BotUpload.state == "done",
               and_(BotUpload.state.in_(IN_FLIGHT), BotUpload.updated_at >= now - STALE_AFTER))


def _queued(db, chat_id, row: BotUpload) -> list[Outgoing]:
    db.expire(row)
    return [send(chat_id, QUEUED), Outgoing("enqueue_bot_take", {"id": row.id})]


def already_received(db, *, tid: str, unique: str, now: datetime) -> BotUpload | None:
    if not str(unique or ""):
        return None
    return (db.query(BotUpload)
            .filter(BotUpload.telegram_user_id == str(tid), BotUpload.file_unique_id == unique,
                    BotUpload.purpose == "take", _taken_state(now))
            .order_by(BotUpload.created_at.asc()).first())


def receive(db, *, tid: str, chat_id, user, info: dict, now: datetime) -> list[Outgoing] | None:
    """Ответ на вложение; `None` — это не дубль, пусть отвечает поток проб без изменений."""
    from app.services.studio_settings import active_book

    if info.get("bad"):
        # Фото или стикер от человека не должны слышать «пришлите аудио» (ревью C2): решает
        # поток проб, а без приглашения — обычный диалог.
        return None
    book = active_book(db)
    if book is None:
        # Диктор с ролью шлёт запись, а студия книгу не выбрала — честный ответ вместо
        # «проба только по приглашению» (спека, часть 2). Приглашённым — пробы как были.
        if _has_approved_role(db, tid=tid, user=user) and not bot_uploads._invitations(db, tid, user):
            return [send(chat_id, NO_BOOK)]
        return None
    roles = my_roles(db, tid=tid, user=user, book=book)
    if not roles:
        return None
    limit = max_download_bytes()
    if info["file_size"] > limit:
        return [send(chat_id, f"The file is larger than {size_text(limit)}. Upload it on the website under Upload: "
                              f"{bot_uploads._site_link()}")]
    invited = bot_uploads._invitations(db, tid, user)
    if not invited and not take_format.allowed_filename(info.get("file_name", ""), kind=info["kind"]):
        return [send(chat_id, take_format.REQUIRED)]
    unique = str(info.get("file_unique_id") or "")
    if unique:
        # Telegram повторяет файл с тем же `file_unique_id`, когда диктор пересылает его ещё
        # раз; второй приём положил бы те же главы вторым дублем и прислал бы тот же отчёт.
        earlier = already_received(db, tid=tid, unique=unique, now=now)
        if earlier is not None:
            return [send(chat_id, f"This file was already received on {earlier.created_at:%d.%m}.")]
    row = BotUpload(telegram_user_id=str(tid), created_at=now, updated_at=now, purpose="take", book_id=book.id,
                    **{k: info[k] for k in ("file_id", "file_unique_id", "kind", "mime_type", "file_name",
                                            "file_size", "duration")})
    db.add(row)
    db.flush()
    cancel = [{"text": "Cancel", "callback_data": f"{BT_PREFIX}x:{row.id}"}]
    if invited:
        # Редкий случай (решение агента): у человека и роль, и открытая проба. Кнопка пробы
        # несёт префикс проб — дальше строку ведёт нынешний поток проб как есть.
        takes = [[{"text": f"Take: «{ch.name}»"[:60], "callback_data": f"{BT_PREFIX}c:{row.id}:{ch.id}"}]
                 for ch in roles]
        auditions = [[{"text": f"Audition: «{ch.name}»"[:60], "callback_data": f"{bot_uploads.BU_PREFIX}c:{row.id}:{ch.id}"}]
                     for ch, _book in invited]
        return [send(chat_id, "Is this a take or an audition?", {"inline_keyboard": [*takes, *auditions, cancel]})]
    ch = roles[0] if len(roles) == 1 else None
    if ch is None and info.get("kind") != "voice":
        ch = _role_from_filename(str(info.get("file_name") or ""), roles, book)
    if ch is not None:
        return _queued(db, chat_id, row) if _claim(db, row.id, ch, now) else []
    rows = [[{"text": str(ch.name)[:60], "callback_data": f"{BT_PREFIX}c:{row.id}:{ch.id}"}] for ch in roles]
    return [send(chat_id, "Which role is this?", {"inline_keyboard": [*rows, cancel]})]


def press(db, *, tid: str, chat_id, data: str, user, now: datetime) -> list[Outgoing]:
    from app.services.studio_settings import active_book

    action, upload_id, character_id = (data[len(BT_PREFIX):].split(":") + ["", ""])[:3]
    row = db.get(BotUpload, upload_id)
    if row is None or row.telegram_user_id != str(tid):
        return []  # чужое нажатие молча гасим
    if action == "x":
        if row.state == "choose":
            row.state, row.updated_at = "cancelled", now
            db.flush()
        if row.state == "cancelled":
            return [send(chat_id, "Cancelled.")]
        if row.state == "done":
            return [send(chat_id, "This file has already been processed.")]
        if row.state in ("failed", "unsorted"):
            return [send(chat_id, "This file has already been sent to the studio.")]
        return [send(chat_id, f"This file is being processed and cannot be cancelled in the bot. If needed, {studio.write_to()}.")]
    if action != "c" or row.state != "choose":
        return []  # второе нажатие: ответ на первое уже ушёл, повтор только запутает
    if row.created_at < now - bot_uploads.CHOICE_TTL:
        return [send(chat_id, "This choice has expired. Please send the file again.")]
    book = active_book(db)
    if book is None or book.id != row.book_id:
        return [send(chat_id, "The studio has changed the active book. Please send the file again.")]
    ch = next((item for item in my_roles(db, tid=tid, user=user, book=book) if item.id == character_id), None)
    if ch is None:
        return [send(chat_id, f"This role is no longer assigned to you. If this is an error, {studio.write_to()}.")]
    if not take_format.allowed_filename(row.file_name, kind=row.kind):
        return [send(chat_id, take_format.REQUIRED)]
    if not _claim(db, row.id, ch, now):
        return []
    return _queued(db, chat_id, row)


def enqueue_take(upload_id: str) -> str | None:
    """Очередь `high`, как у проб: диктор ждёт отчёт, а не ночной прогон конвейера."""
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    with SessionLocal() as db:
        row = db.get(BotUpload, str(upload_id))
        if row is None:
            return None
        language = snapshot_language(db, row)

    return enqueue_tracked_task(
        "high", "app.worker_tasks.perform_bot_take_task", SessionLocal, BackgroundRun,
        job_kind="bot_take", entity_type="bot_upload", entity_id=str(upload_id),
        run_key=f"bot_take:{upload_id}", upload_id=str(upload_id),
        meta={"language": language}, language=language,
    )


def snapshot_language(db, row, *, language: str | None = None, workdir: str | None = None) -> str:
    """Сохраняем профиль до первой оплаты; ручной повтор использует уже оплаченный кэш."""
    import json
    import glob
    import os
    from app.services.asr_run import asr_language

    try:
        meta = json.loads(row.result_json or "{}")
    except (TypeError, ValueError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    if meta.get("language"):
        return asr_language(meta["language"])
    book = db.get(ScriptBook, row.book_id)
    selected = asr_language(language if language is not None else getattr(book, "language", "ru"))
    cached = workdir or takes_workdir(row.id)
    if os.path.exists(os.path.join(cached, "heard.json")) or glob.glob(os.path.join(cached, "part-*.json")):
        # У старого оплаченного кэша нет профиля. Не выдаём его за новый английский
        # транскрипт и не удаляем ради повторного списания; сохраняем прежний RU-режим.
        selected = "ru"
        meta["language_source"] = "legacy_cache_default"
    meta["language"] = selected
    row.result_json = json.dumps(meta, ensure_ascii=False)
    db.commit()
    return selected


# --- Обработка в воркере (спека, часть 3, «Обработка» … «Учёт») ---------------------------------

FAILED_TEXT = "The file could not be processed. The studio will review it; you do not need to send it again."
#: сколько ненайденных реплик главы называть диктору поимённо; дальше — «и ещё K»
MISSING_SHOWN = 5
#: длинная реплика в отчёте режется: диктору хватит начала, чтобы узнать её
MISSING_CHARS = 60


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _chapters_word(n: int) -> str:
    return f"{n} {'chapter' if n == 1 else 'chapters'}"


def _quote(text: str) -> str:
    text = " ".join(str(text or "").split())
    if len(text) > MISSING_CHARS:
        text = text[:MISSING_CHARS - 1].rstrip() + "…"
    return f"«{text}»"


def take_report(role: str, book_title: str, chapters: list[dict]) -> str:
    """Одно сообщение диктору на весь файл — вместо писем о пропусках по каждой главе.

    Подсказка в конце — только когда что-то не нашлось: при полном совпадении она
    заставила бы диктора искать, что же он пропустил.
    """
    out = [f"Received — «{role}», «{book_title}», {_chapters_word(len(chapters))}:"]
    gaps = False
    for item in chapters:
        index, found, total = int(item["index"]), int(item["found"]), int(item["total"])
        missing = list(item.get("missing") or [])
        if not missing and found >= total:
            if total == 1:
                out.append(f"ch. {index} — the only line")
            else:
                out.append(f"ch. {index} — all {total} {('line' if total == 1 else 'lines')}")
            continue
        gaps = True
        line = f"ch. {index} — {found} of {total}"
        if missing:
            line += ", missing: " + ", ".join(_quote(text) for text in missing[:MISSING_SHOWN])
            if len(missing) > MISSING_SHOWN:
                line += f" and {len(missing) - MISSING_SHOWN} more"
        out.append(line)
    if gaps:
        out.append("If a recorded line was not found, no action is needed; the studio will review it.")
    return "\n".join(out)


def owner_summary(actor: str, role: str, chapters: list[dict]) -> str:
    """Коротко владельцу: кто, роль, какие главы и где пропуски — подробности в отчётах по главам.

    Номера глав, а не их число: четыре файла одного диктора (главы 5, 9, 16, 18) давали
    четыре одинаковых «1 глава», и владелец принял их за повтор сообщения (29.10)."""
    numbers = ", ".join(str(item["index"]) for item in chapters)
    where = f"ch. {numbers}" if len(chapters) == 1 else f"chapters {numbers}"
    head = f"Take from bot: {actor}, «{role}», {where}."
    gaps = [f"ch. {item['index']} — {int(item['total']) - int(item['found'])}"
            for item in chapters if int(item["found"]) < int(item["total"])]
    return head + ("\nMissing lines: " + ", ".join(gaps) + "." if gaps else "\nAll lines were found.")


def _extension(row: BotUpload) -> str:
    import os

    ext = os.path.splitext(str(row.file_name or ""))[1].lower()
    if ext:
        return ext
    known = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-wav": ".wav",
             "audio/wav": ".wav", "audio/flac": ".flac"}
    return known.get(str(row.mime_type or "").lower(), ".ogg" if row.kind == "voice" else ".bin")


def _source_name(row: BotUpload) -> str:
    """Имя, которое прислал диктор; у голосового имени нет — придумываем по виду."""
    return str(row.file_name or "") or f"voice{_extension(row)}"


def _already_taken(db, row: BotUpload, now: datetime) -> BotUpload | None:
    """Та же запись (по `file_unique_id`), уже принятая этим человеком, — второй раз не платим.

    Приём (`receive`) отсекает повтор по `file_unique_id`, но две строки могли встать в
    очередь до того, как первая стала `done` (ревью задачи 7). Из двух живых задач
    уступает созданная позже: так две одновременные не отменят друг друга, а мёртвая
    (`STALE_AFTER`) не мешает прислать файл заново.
    """
    from sqlalchemy import and_, or_

    if not str(row.file_unique_id or ""):
        return None
    earlier_than_me = or_(BotUpload.created_at < row.created_at,
                          and_(BotUpload.created_at == row.created_at, BotUpload.id < row.id))
    return (db.query(BotUpload)
            .filter(BotUpload.telegram_user_id == row.telegram_user_id, BotUpload.purpose == "take",
                    BotUpload.id != row.id, BotUpload.file_unique_id == row.file_unique_id,
                    or_(BotUpload.state == "done",
                        and_(_taken_state(now), BotUpload.state.in_(IN_FLIGHT), earlier_than_me)))
            .order_by(BotUpload.created_at.asc()).first())


def takes_workdir(upload_id: str) -> str:
    """Постоянный рабочий каталог строки учёта — вне корня аудио.

    Корень аудио целиком уходит во внешний бэкап (`rclone copy --ignore-existing`, каждые
    полчаса), и двухгигабайтные временные файлы улетали бы на Яндекс.Диск навсегда. Каталог
    `data/spool` бэкап исключает (`--exclude "spool/**"` в infra/offsite/offsite.sh).
    Папка «Unsorted» с работой диктора остаётся в корне аудио — её как раз беречь.
    """
    import os

    root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "data", "spool", "bot_takes")
    return os.path.join(root, str(upload_id))


def _same_content(db, row: BotUpload, now: datetime, workdir: str) -> tuple[BotUpload | None, str]:
    """Та же запись по md5 — Telegram даёт новый `file_unique_id` файлу, пересланному
    документом, а содержимое то же. Возвращает (строку, ответ диктору).

    «Не разобрано» или сбой с сохранённым распознаванием — тоже повтор: файл у студии, он оплачен,
    второй раз его не распознаём.
    """
    import os

    from sqlalchemy import and_, or_

    base = db.query(BotUpload).filter(BotUpload.telegram_user_id == row.telegram_user_id,
                                      BotUpload.purpose == "take", BotUpload.id != row.id,
                                      BotUpload.md5 == row.md5)
    earlier_than_me = or_(BotUpload.created_at < row.created_at,
                          and_(BotUpload.created_at == row.created_at, BotUpload.id < row.id))
    done = base.filter(or_(BotUpload.state == "done",
                           and_(_taken_state(now), BotUpload.state.in_(IN_FLIGHT), earlier_than_me))) \
        .order_by(BotUpload.created_at.asc()).first()
    if done is not None:
        return done, f"This file was already received on {done.created_at:%d.%m}."
    parent = os.path.dirname(os.path.abspath(workdir))
    # `failed` тоже: файл не лёг, но распознавание уже оплачено и лежит в каталоге строки.
    for other in base.filter(BotUpload.state.in_(("unsorted", "failed"))).order_by(BotUpload.created_at.asc()).all():
        if os.path.exists(os.path.join(parent, other.id, "heard.json")):
            return other, "This file has already been sent to the studio."
    return None, ""


class _NotFound(Exception):
    """В записи не нашлось ни одной засчитанной главы роли."""


class _Unsplittable(Exception):
    """Главы в записи не по порядку: реплики есть, но резать нельзя — разбирает человек.
    Диктору тут нельзя сказать «реплик не нашёл»: он их записал."""


class _RecognitionFailed(Exception):
    """Распознавание упало: сохранённые части нужны повтору, их не стираем."""


def _role_chapters(db, ch: Character, *, language: str | None = None) -> list[tuple[int, str, str, list[str]]]:
    """(номер, id главы, метка, реплики роли) по возрастанию номера — вход `chapter_blocks`."""
    from app.models import ScriptChapter
    from app.services import asr_run
    from app.services.role_deadlines import role_chapters

    labels = role_chapters(db, ch)
    if not labels:
        return []
    rows = db.query(ScriptChapter).filter(ScriptChapter.book_id == ch.book_id,
                                          ScriptChapter.chapter_index.in_(sorted(labels))).all()
    ids = {int(item.chapter_index): str(item.id) for item in rows}
    return [(index, ids[index], labels[index], asr_run.expected_lines(db, ids[index], str(ch.name or ""), language=language))
            for index in sorted(labels)]


def _chapter_result(db, job, block, role: str) -> dict:
    """Итог куска для отчёта — по сверке самого куска, в репликах-абзацах.

    Сверка идёт по кускам, а реплика — абзац: слова автора режут её на два-три куска, и
    счёт кусков разошёлся бы с Читалкой (ловушка «Реплика — это абзац», 112% 24.10).
    Абзац найден, только если найдены все его куски. Названия ненайденного — тексты
    кусков: диктору так проще узнать, какие слова не услышаны.
    """
    import json

    from app.services import asr_run

    alignment = json.loads(str(getattr(job, "alignment_json", "") or "{}")) if job is not None else {}
    lines = alignment.get("lines")
    if getattr(job, "status", "") != "done" or lines is None:
        return {"index": block.chapter_index, "found": block.found, "total": block.total, "missing": []}
    paragraphs = asr_run.expected_line_segments(db, block.chapter_id, role, language=getattr(job, "language", None))
    if len(paragraphs) != len(lines):
        # Разметку поменяли между сверкой и отчётом (или абзацев нет) — честнее счёт кусков,
        # чем чужие абзацы.
        paragraphs = [str(n) for n in range(len(lines))]
    heard: dict[str, bool] = {}
    for line, paragraph in zip(lines, paragraphs):
        heard[paragraph] = heard.get(paragraph, True) and bool(line.get("matched"))
    return {"index": block.chapter_index, "found": sum(heard.values()), "total": len(heard),
            "missing": [str(line.get("text") or "") for line in lines if not line.get("matched")]}


def _job_of(db, audio_id: str):
    from app.models import AsrJob

    return db.query(AsrJob).filter(AsrJob.audio_file_id == audio_id).one_or_none()


def _mark_failed(db, upload_id: str, now: datetime) -> None:
    """Строка не должна остаться «в работе» после неожиданного исключения: иначе она
    навсегда держит повтор файла и выглядит живой. Сессия могла сломаться вместе с
    исключением — тогда пишем отдельной."""
    from sqlalchemy.orm import Session

    values = {"state": "failed", "updated_at": now}
    try:
        db.rollback()
        db.query(BotUpload).filter(BotUpload.id == upload_id, BotUpload.state == "processing") \
            .update(values, synchronize_session=False)
        db.commit()
        return
    except Exception:  # noqa: BLE001 — пробуем свежей сессией
        pass
    with Session(bind=db.get_bind()) as fresh:
        fresh.query(BotUpload).filter(BotUpload.id == upload_id, BotUpload.state == "processing") \
            .update(values, synchronize_session=False)
        fresh.commit()


def process(db, upload_id: str, *, fetch, transcribe_part, send_text, notify_owner, workdir: str,
            now=None, clock=None, language: str | None = None) -> str:
    """Задача воркера: дубль из бота — скачать, распознать раз, разложить по главам, отчитаться.

    Возвращает `done` | `unsorted` | `failed` | `skip`. Распознавание — единственный
    платный шаг, и он делается один раз на файл: куски сверяются срезом уже услышанного,
    повтор того же файла не распознаётся, а всё услышанное (`heard.json`, части) лежит в
    `workdir` (он постоянный для строки учёта) до успеха — ручной повтор не платит снова.
    Всё, что не легло дублем, лежит в «не разобрано» — работа диктора не теряется молча
    (спека, «Анти-цели»).

    Строку захватывает `queued → processing` одной записью: два запуска одной задачи
    (повтор RQ, ручной перезапуск) не пойдут оба. Любое неожиданное исключение переводит
    строку в `failed`, а не оставляет её «в работе».

    Живая задача отмечается (`updated_at`) после каждой распознанной части и каждой
    легшей главы: колбаса Рассказчика на 15 ч идёт дольше `STALE_AFTER`, и без отметок
    повтор файла посчитал бы её мёртвой и заплатил бы второй раз. `clock` — часы
    отметок (подменяются в тестах).
    """
    from app.time_utils import utcnow_naive

    clock = clock or utcnow_naive
    now = now or clock()
    upload_id = str(upload_id or "")
    claimed = (db.query(BotUpload)
               .filter(BotUpload.id == upload_id, BotUpload.state == "queued", BotUpload.purpose == "take")
               .update({"state": "processing", "updated_at": now}, synchronize_session=False))
    db.commit()
    if claimed != 1:
        return "skip"
    try:
        row = db.get(BotUpload, upload_id)
        language = snapshot_language(db, row, language=language, workdir=workdir)
        if not take_format.allowed_filename(row.file_name, kind=row.kind):
            return _reject_format(db, row, send_text, now)
        return _process(db, upload_id, fetch=fetch, transcribe_part=transcribe_part, send_text=send_text,
                        notify_owner=notify_owner, workdir=workdir, now=now, clock=clock, language=language)
    except BaseException:
        _mark_failed(db, upload_id, now)
        raise


def _reject_format(db, row, send_text, now) -> str:
    row.state, row.updated_at = "failed", now
    db.commit()
    send_text(row.telegram_user_id, take_format.REQUIRED)
    return "failed"


def _beat(db, upload_id: str, at) -> None:
    """Отметка «задача жива»: строка в работе не должна выглядеть мёртвой (`STALE_AFTER`)."""
    db.query(BotUpload).filter(BotUpload.id == upload_id, BotUpload.state == "processing") \
        .update({"updated_at": at}, synchronize_session=False)
    db.commit()


def _process(db, upload_id: str, *, fetch, transcribe_part, send_text, notify_owner, workdir: str, now,
             clock, language: str = "ru") -> str:
    import hashlib
    import json
    import logging
    import os
    import shutil

    from app.models import AuditLog, ScriptChapter
    from app.services import asr_run, audio_integrity, audio_storage
    from app.services.audio_probe import probe_audio_file
    from app.services.audio_uploads import TAKE, derive_book_code, fix_number, parse_batch_audio_filename, store_audio_file
    from app.services.shared_runtime import normalize_role_label, safe_name
    from app.services.take_split import OutOfOrder, chapter_blocks, cut_audio, name_mismatch, plan_cuts, slice_transcript
    from app.v2.cast_ops import parse_actor_name

    log = logging.getLogger(__name__)
    row = db.get(BotUpload, upload_id)
    db.refresh(row)
    tid = row.telegram_user_id

    def duplicate(text: str) -> str:
        row.state, row.updated_at = "cancelled", now
        db.commit()
        send_text(tid, text)
        return "skip"

    earlier = _already_taken(db, row, now)
    if earlier is not None:
        return duplicate(f"This file was already received on {earlier.created_at:%d.%m}.")

    ch = db.get(Character, row.character_id)
    book = db.get(ScriptBook, row.book_id) or (db.get(ScriptBook, ch.book_id) if ch is not None else None)
    if book is not None and book.voiced_at is not None:
        # Книгу объявили озвученной, пока файл ждал в очереди: в законченную книгу не кладём.
        return duplicate("The book has already been recorded. This recording was not accepted.")
    role = str(row.role_name or "") or str(getattr(ch, "name", "") or "")
    actor = str(row.actor_name or "") or parse_actor_name(getattr(ch, "actor_name", "") or "")[0]
    code = derive_book_code(str(getattr(book, "title", "") or ""))
    who = f"{actor or tid}, «{role}»"
    # Повтор строки после частичного сбоя: главы, уже легшие дублями, второй раз не кладём.
    try:
        previous = json.loads(row.result_json or "{}").get("stored") or []
    except ValueError:
        previous = []
    already = {int(item["chapter"]): str(item["audio_id"]) for item in previous
               if isinstance(item, dict) and item.get("audio_id")}

    def fail(reason: str) -> str:
        row.state, row.updated_at = "failed", now
        db.commit()
        notify_owner(f"Bot take was not processed: {who}. Reason: {reason}. "
                     f"The file remains only in Telegram (upload record {upload_id}).")
        send_text(tid, FAILED_TEXT)
        return "failed"

    # character_id и book_id из очереди — авторитетные границы до скачивания/STT.
    # Иначе неоднозначный alias обнаруживался лишь после оплаченного распознавания.
    try:
        from app.services.role_identity import book_roles, CHARACTER_PREFIX, RoleIdentityError
        from app.services.audio_naming import book_token
        if ch is None or book is None or str(ch.book_id) != str(book.id) or not actor:
            raise RoleIdentityError("audio_book_scope_mismatch")
        identities = book_roles(db, book.id)
        if not code or sum(book_token(derive_book_code(str(title or ""))) == book_token(code)
                           for title, in db.query(ScriptBook.title).all()) != 1:
            raise RoleIdentityError("ambiguous_book_code")
        if identities.typed:
            expected_ref = identities.character_ref(ch)
            if identities.ref(ch.name) != expected_ref:
                raise RoleIdentityError("ambiguous_role_identity")
            role = str(ch.name or "")
        for audio_id in already.values():
            cached = db.get(AudioFile, audio_id)
            if cached is None or not identities.owns_audio(cached):
                raise RoleIdentityError("cached_audio_scope_mismatch")
        role_chapters = _role_chapters(db, ch, language=language)
    except (RoleIdentityError, LookupError) as exc:
        return fail(str(exc))
    os.makedirs(workdir, exist_ok=True)
    source = os.path.join(workdir, f"source{_extension(row)}")
    finished = False
    try:
        # 1. Скачать потоком: со своего узла Bot API приходят файлы до 2 ГБ.
        try:
            digest = hashlib.md5()
            with fetch(row.file_id) as stream, open(source, "wb") as target:
                while chunk := stream.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
        except Exception as exc:  # noqa: BLE001 — до скачивания класть в «не разобрано» нечего
            db.rollback()
            log.warning("Дубль из бота %s не скачан: %s", upload_id, type(exc).__name__)
            return fail(f"Download failed: {type(exc).__name__}: {exc}"[:300])
        # До оплаченного распознавания проверяем сам файл: расширение .wav у MP3
        # не делает его исходником для сведения. Неподходящее аудио просим заменить.
        # Нечитаемый файл по-прежнему попадёт в «не разобрано» ниже: его не теряем
        # и не выдаём отсутствие результата ffprobe за определённый формат.
        probe = probe_audio_file(source)
        if probe and not take_format.allowed_audio(source, probe):
            return _reject_format(db, row, send_text, now)
        row.md5, row.updated_at = digest.hexdigest(), now
        same, text = _same_content(db, row, now, workdir)
        if same is not None:
            return duplicate(text)
        db.commit()

        stored: list[dict] = list({"chapter": k, "audio_id": v} for k, v in already.items())
        stepped: set[str] = set()

        def chapter_steps(chapter) -> None:
            """Дальше — как после загрузки на сайте, но без писем о пропусках по главам."""
            if chapter is None or str(chapter.id) in stepped:
                return
            stepped.add(str(chapter.id))
            try:
                label = str(chapter.chapter_title or "").strip()
                take_ids = [str(item.id) for item in db.query(AudioFile).filter(
                    AudioFile.kind == TAKE, AudioFile.chapter == label).all() if identities.owns_audio(item)]
                asr_run.after_new_takes(db, chapter, take_ids=take_ids)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("Шаги главы %s после дубля из бота не прошли", chapter.id)
            try:
                if audio_integrity.chapter_needs_verification(db, str(chapter.id)):
                    audio_integrity.enqueue_verify_for_chapter(str(chapter.id))
            except Exception:
                log.exception("Не удалось поставить сверку главы %s после дубля из бота", chapter.id)

        try:
            if ch is None or book is None or not actor:
                raise LookupError("The role, book or actor for this upload could not be found")
            duration = float(probe.get("duration_seconds") or 0.0)
            if duration <= 0:
                # Не аудио (или битый файл): не платим за распознавание мусора.
                raise ValueError("ffprobe could not read the duration; the file is not audio or is corrupt")

            # 2. Распознать целиком — раз. Итог сохраняется рядом с частями: сбой ниже
            #    (диск, база) при ручном повторе не заплатит ещё раз.
            heard_path = os.path.join(workdir, "heard.json")
            if os.path.exists(heard_path):
                with open(heard_path, encoding="utf-8") as handle:
                    heard = json.load(handle)
            else:
                try:
                    def part(path):
                        result = transcribe_part(path)
                        _beat(db, upload_id, clock())
                        return result
                    heard = transcribe_whole(source, transcribe_part=part, workdir=workdir,
                                             duration=duration)
                except Exception as exc:
                    raise _RecognitionFailed(f"Transcription: {type(exc).__name__}: {exc}") from exc
                tmp = heard_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(heard, handle, ensure_ascii=False)
                os.replace(tmp, heard_path)
            segments = heard.get("segments") or []

            # 3. Главы роли в записи и куски.
            parsed = parse_batch_audio_filename(_source_name(row), default_book_code=code, default_actor_name="",
                                                normalize_role_label=normalize_role_label)
            named = int(parsed.get("filename_chapter_index") or 0)
            is_fix = fix_number(_source_name(row)) is not None
            blocks = chapter_blocks(role_chapters, segments, language=language)
            try:
                cuts = plan_cuts(blocks, duration, filename_chapter=named, is_fix=is_fix)
            except OutOfOrder as exc:
                raise _Unsplittable(f"Chapters are out of order in the recording ({exc}); the recording was not split") from exc
            if not cuts:
                raise _NotFound("No lines for this role were found in the recording")
            mismatch = name_mismatch(cuts, named)

            # 4. Каждый кусок — дубль своей главы, сверенный срезом услышанного.
            ext = _extension(row)
            results: list[dict] = []
            chapters: list[ScriptChapter] = []
            for n, (block, start, end) in enumerate(cuts):
                chapter = db.get(ScriptChapter, block.chapter_id)
                if block.chapter_index in already:
                    job = _job_of(db, already[block.chapter_index])
                    if job is None or job.status != "done":
                        # Прошлый прогон положил дубль и упал на его сверке: сверяем сейчас
                        # тем же срезом услышанного — бесплатно, `heard.json` уже есть.
                        job = asr_run.run_asr_for_take(
                            db, already[block.chapter_index], notify=False,
                            language=language,
                            transcribe=lambda _path, s=start, e=end: slice_transcript(segments, s, e),
                        )
                        db.commit()
                else:
                    if len(cuts) == 1:
                        piece, original = source, _source_name(row)
                    else:
                        piece, original = os.path.join(workdir, f"piece-{n}{ext}"), f"{block.label}{ext}"
                        cut_audio(source, start, end, piece)
                    with open(piece, "rb") as handle:
                        audio = store_audio_file(
                            db, source=handle, mime_type=row.mime_type or "audio/mpeg", original_filename=original,
                            book_code=code, chapter=block.label, role=role, actor_name=actor, kind=TAKE,
                            safe_name=safe_name, size_hint=os.path.getsize(piece),
                        )
                    db.commit()
                    stored.append({"chapter": block.chapter_index, "audio_id": audio.id})
                    job = asr_run.run_asr_for_take(
                        db, audio.id, notify=False,
                        language=language,
                        transcribe=lambda _path, s=start, e=end: slice_transcript(segments, s, e),
                    )
                    db.commit()
                _beat(db, upload_id, clock())
                results.append(_chapter_result(db, job, block, role))
                chapters.append(chapter)

            # 5. Шаги главы — после того как легли все куски.
            for chapter in chapters:
                chapter_steps(chapter)
        except Exception as exc:  # noqa: BLE001 — любой сбой после скачивания: файл в «не разобрано»
            db.rollback()
            if not isinstance(exc, (_NotFound, _Unsplittable)):
                log.warning("Дубль из бота %s не разложен: %s", upload_id, exc)
            ours = isinstance(exc, (_NotFound, _Unsplittable, _RecognitionFailed))
            reason = str(exc) if ours else f"{type(exc).__name__}: {exc}"
            # Уже легшие куски — полноценные дубли: их главы проходят те же шаги, что и
            # при успехе, иначе сессия главы молча не узнала бы о новом дубле.
            for item in stored:
                chapter = db.query(ScriptChapter).filter(ScriptChapter.book_id == book.id,
                                                         ScriptChapter.chapter_index == item["chapter"]).first() \
                    if book is not None else None
                chapter_steps(chapter)
            saved = ""
            if stored:
                numbers = ", ".join(str(item["chapter"]) for item in sorted(stored, key=lambda i: i["chapter"]))
                ids = ", ".join(item["audio_id"] for item in stored)
                saved = f" Already saved as takes: ch. {numbers} (id: {ids}); retrying this upload will not duplicate them."
            name = safe_name(_source_name(row))
            # Папка книги — тот же токен, что в `audio_mirror.storage_key`: «КП» кириллицей
            # иначе завело бы `rec/КП/` рядом с настоящим `rec/KP/`.
            key = f"{book_token(code) or 'BOOK'}/Unsorted/{upload_id}_{name}"
            try:
                with open(source, "rb") as handle:
                    audio_storage.write_stream(key, handle)
            except Exception as write_exc:  # noqa: BLE001
                row = db.get(BotUpload, upload_id)
                row.result_json = json.dumps({"reason": reason, "stored": stored, "language": language}, ensure_ascii=False)
                return fail(f"{reason[:200]}; could not save to unsorted: "
                            f"{type(write_exc).__name__}: {write_exc}.{saved}")
            row = db.get(BotUpload, upload_id)
            row.state, row.stored_key, row.updated_at = "unsorted", key[:255], now
            row.result_json = json.dumps({"reason": reason, "stored": stored, "language": language}, ensure_ascii=False)
            db.add(AuditLog(user_id="", entity_type="bot_upload", entity_id=upload_id, action="bot_take_unsorted",
                            created_at=now, payload_json=json.dumps({"reason": reason, "stored_key": key,
                                                                     "stored": stored}, ensure_ascii=False)))
            db.commit()
            notify_owner(f"Bot take could not be split: {who}. Reason: {reason}. File: {key}.{saved}")
            send_text(tid, f"No lines were found in the file for role «{role}». The studio has been notified and will contact you."
                      if isinstance(exc, _NotFound) and not stored else FAILED_TEXT)
            return "unsorted"

        # 6. Отчёт и учёт.
        audio_ids = [item["audio_id"] for item in sorted(stored, key=lambda i: i["chapter"])]
        row = db.get(BotUpload, upload_id)
        row.state, row.updated_at, row.audio_id = "done", now, audio_ids[0] if audio_ids else ""
        row.result_json = json.dumps({"chapters": results, "audio_ids": audio_ids, "stored": stored,
                                      "mismatch": mismatch or 0, "language": language}, ensure_ascii=False)
        db.add(AuditLog(user_id="", entity_type="bot_upload", entity_id=upload_id, action="bot_take_stored",
                        created_at=now, payload_json=json.dumps({"audio_ids": audio_ids, "telegram_user_id": tid},
                                                                ensure_ascii=False)))
        db.commit()
        try:
            from app.services.released_alerts import notify_takes_in_released

            notify_takes_in_released(db, db.query(AudioFile).filter(AudioFile.id.in_(audio_ids)).all())
        except Exception:
            log.exception("Не удалось сообщить о дубле из бота в сданной главе")
        finished = True
        summary = owner_summary(actor, role, results)
        if mismatch:
            summary += (f"\nThe filename says chapter {mismatch}, but the recording matches chapter {cuts[0][0].chapter_index}; "
                        f"saved under the chapter found in the recording.")
        send_text(tid, take_report(role, str(book.title or ""), results))
        notify_owner(summary)
        return "done"
    finally:
        # Услышанное оплачено: пока файл не разложен до конца, `heard.json` и части
        # остаются для ручного повтора, при любой причине сбоя. Копия файла и куски — не
        # нужны (файл лежит в «не разобрано» или в Telegram).
        if finished:
            shutil.rmtree(workdir, ignore_errors=True)
        else:
            for name in os.listdir(workdir) if os.path.isdir(workdir) else []:
                if not name.endswith(".json"):
                    os.remove(os.path.join(workdir, name))
