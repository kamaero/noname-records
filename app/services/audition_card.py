"""Карточка пробы в боте (пакет C1, спека 2026-10-05-bot-auditions).

Пробу слушали, оценивали и утверждали только на сайте; автору приходило «кто-то
пробуется» текстом. Теперь владельцу и автору приходит аудио с оценкой 1–10 и «Утвердить» —
те же действия, что в листе проб. Отправка — из воркера: загрузка файла в Telegram идёт
секунды, а uvicorn один.
"""
from __future__ import annotations

from app.config import settings
from datetime import timedelta

from app.models import AudioFile, BotCommentWait, Character, ScriptBook, TelegramAuthAccount
from app.services.audio_storage import resolve_path
from app.services.audio_uploads import derive_book_code
from app.services.audition_rejections import character_for_role
from app.services.audition_scores import COMMENT_MAX_CHARS, REASON_LABELS, reaction_notice, scores_by_pair


def storage_unavailable_for(location: str) -> bool:
    """Файл на NAS, а NAS не отвечает. Флаг `nas_online()` живёт в памяти веб-процесса,
    в воркере он всегда «не знаем» (ревью C1) — поэтому разовая проба с таймаутом."""
    if str(location or "local") != "nas":
        return False
    from app.services import nas_health

    return not nas_health.reachable_now()


AC_PREFIX = "ac:"
#: предел `sendAudio` для бота; больше — ссылка на лист проб
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def pair_of(db, audio: AudioFile) -> tuple[ScriptBook | None, Character | None]:
    """Книга по коду и персонаж по всем написаниям роли — как лист проб."""
    book = next((b for b in db.query(ScriptBook).all()
                 if derive_book_code(str(b.title or "")) == str(audio.book_code or "")), None)
    if book is None:
        return None, None
    cast = db.query(Character).filter(Character.book_id == book.id).all()
    return book, character_for_role(cast, str(audio.role or ""))


def _is_voice(audio: AudioFile) -> bool:
    return str(audio.codec or "").lower() == "opus" or str(audio.original_filename or "").lower().endswith(".ogg")


def caption(db, audio: AudioFile) -> str:
    book, ch = pair_of(db, audio)
    role = str(ch.name if ch is not None else audio.role or "")
    title = str(getattr(book, "title", "") or "")
    voice = " · voice message" if _is_voice(audio) else ""
    scores = scores_by_pair(db, book.id).get((str(audio.role or ""), str(audio.actor_name or ""))) if book else None
    scores = scores or {}

    def value(lane: str) -> str:
        row = scores.get(lane)
        return str(row["score"]) if row else "—"

    return (f"🎙 Audition «{role}» («{title}») — {audio.actor_name}{voice}\n"
            f"Author: {value('author')}   Studio: {value('studio')}")


def keyboard(audio_id: str, *, approve: bool = True) -> dict:
    rows = [[{"text": str(n), "callback_data": f"{AC_PREFIX}s{n}:{audio_id}"} for n in range(1, 6)],
            [{"text": str(n), "callback_data": f"{AC_PREFIX}s{n}:{audio_id}"} for n in range(6, 11)]]
    if approve:
        rows.append([{"text": "✅ Approve for role", "callback_data": f"{AC_PREFIX}a:{audio_id}"}])
    return {"inline_keyboard": rows}


def recipients(db) -> list[tuple[str, str]]:
    """Владелец — шкала студии, автор — шкала автора (как `notify_author_about_auditions`)."""
    found: list[tuple[str, str]] = []
    owner = str(settings.owner_telegram_id or "").strip()
    if owner:
        found.append((owner, "studio"))
    for row in db.query(TelegramAuthAccount).filter(TelegramAuthAccount.role == "author",
                                                     TelegramAuthAccount.is_active == "true").all():
        chat = str(row.telegram_user_id or "").strip()
        if chat and chat not in {c for c, _ in found}:
            found.append((chat, "author"))
    return found


#: кодеки, которые плеер Telegram играет сам; остальное (WAV) — через MP3-копию
PLAYABLE = ("mp3", "aac", "opus")


def to_mp3(source: str) -> str:
    """Копия для прослушивания: MP3 192 кбит/с во временном файле, как демо дикторов.
    Пробы приходят WAV до 40 МБ, а плеер Telegram играет MP3/M4A — WAV пришёл бы файлом."""
    import subprocess
    import tempfile

    import os

    handle = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    handle.close()
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-vn", "-codec:a", "libmp3lame", "-b:a", "192k",
                        handle.name], check=True, timeout=300)
    except Exception:
        os.remove(handle.name)  # недописанная копия не должна копиться во временном каталоге
        raise
    return handle.name


def send_card(db, audio_id: str, *, send_audio, send_text, file_id: str = "", transcode=to_mp3) -> int:
    """Карточка каждому адресату. Ни один сбой не должен прятать пробу (ревью C1): не
    сжалась, не прочиталась, NAS молчит, Telegram отказал — адресату текст со ссылкой."""
    import logging
    import os

    log = logging.getLogger(__name__)
    audio = db.get(AudioFile, str(audio_id or ""))
    if audio is None:
        return 0
    text = caption(db, audio)
    link = f"{settings.app_base_url.rstrip('/')}/app/auditions"
    book, ch = pair_of(db, audio)
    role = str(ch.name if ch is not None else audio.role or "")
    voice = _is_voice(audio)
    path = copy = ""
    fallback = ""
    if not file_id:
        # Старые записи лежат на NAS (`location`): путь — с тем же `location`, что у плеера
        # сайта; NAS не отвечает — ссылка вместо аудио, а не упавшая задача.
        location = str(getattr(audio, "location", None) or "local")
        if storage_unavailable_for(location):
            fallback = f"{text}\n\nThe file is unavailable because NAS storage is not responding. Listen on the website: {link}"
        else:
            try:
                path = resolve_path(audio.stored_key, location=location)
                if str(audio.codec or "").lower() not in PLAYABLE:
                    path = copy = transcode(path)  # одна копия на всех адресатов
                size = os.path.getsize(path) if copy else int(audio.size_bytes or 0)
            except Exception as exc:  # noqa: BLE001 — битый файл не должен прятать пробу
                log.warning("Карточка пробы %s: файл не подготовлен: %s", audio.id, type(exc).__name__)
                fallback = f"{text}\n\nCould not prepare the file. Listen on the website: {link}"
            else:
                if size > MAX_UPLOAD_BYTES:
                    fallback = f"{text}\n\nThe file exceeds 50 MB. Listen and rate on the website: {link}"
    ext = ".ogg" if voice and not copy else os.path.splitext(path or str(audio.original_filename or ""))[1] or ".mp3"
    # имя трека в плеере Telegram — роль и актёр, а не временный файл («tmpab12.mp3»)
    filename = f"{role} — {audio.actor_name}{ext}"
    delivered = 0
    try:
        for chat, _lane in recipients(db):
            ok = False
            if not fallback:
                ok = send_audio(chat, caption=text, reply_markup=keyboard(audio.id), file_id=file_id, path=path,
                                voice=voice and not copy, filename=filename)
            if not ok:
                ok = send_text(chat, fallback or f"{text}\n\nThe audio could not be delivered. Listen on the website: {link}")
            delivered += bool(ok)
    finally:
        if copy and os.path.exists(copy):
            os.remove(copy)
    return delivered


def enqueue_card(audio_id: str, file_id: str = "") -> str | None:
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    return enqueue_tracked_task(
        "high", "app.worker_tasks.perform_audition_card_task", SessionLocal, BackgroundRun,
        job_kind="audition_card", entity_type="audio_file", entity_id=str(audio_id),
        run_key=f"audition_card:{audio_id}", audio_id=str(audio_id), file_id=str(file_id or ""),
    )


#: После низкой оценки поясняем, что голос может быть хорошим, но не подходить герою:
#: без этого кнопка причины воспринималась как приговор качеству голоса.
WHY_LOW = "What does not fit this character? The voice may be excellent; this is about how the character should sound."
WHY_HIGH = "A few words for the studio: how should the character sound? This will not be sent to the actor."
COMMENT_BUTTON = "✍️ In your own words"
#: сколько бот ждёт текст после «Своими словами»; дольше — сообщение уже не про пробу
COMMENT_TTL = timedelta(minutes=30)


def ask_keyboard(audio_id: str, *, reasons: bool) -> dict:
    """Причины — по одной в строке: подписи длинные, в ряд на телефоне не влезут."""
    rows = [[{"text": label, "callback_data": f"{AC_PREFIX}r{key}:{audio_id}"}]
            for key, label in REASON_LABELS.items()] if reasons else []
    rows.append([{"text": COMMENT_BUTTON, "callback_data": f"{AC_PREFIX}c:{audio_id}"}])
    return {"inline_keyboard": rows}


def _owner_notice(db, *, tid: str, pair: dict, role_title: str) -> list:
    """Реакция автора — владельцу отдельным сообщением (решение 11.10). Себе не пишем."""
    from app.services.bot_out import send

    owner = str(settings.owner_telegram_id or "").strip()
    if not owner or owner == str(tid or ""):
        return []
    text = reaction_notice(db, **pair, role_title=role_title)
    return [send(owner, text)] if text else []


def drop_comment_wait(db, tid: str) -> None:
    db.query(BotCommentWait).filter(BotCommentWait.tid == str(tid or "")).delete()


def take_comment(db, *, tid: str, chat_id, text: str, user, roles: set[str], now) -> list | None:
    """Текст после «Своими словами» — комментарий к пробе. None — бот ничего не ждал,
    сообщение разбирает остальной диалог."""
    from app.services.audition_scores import ScoreError, set_comment
    from app.services.bot_out import send

    wait = db.get(BotCommentWait, str(tid or ""))
    if wait is None:
        return None
    db.delete(wait)
    if now - wait.created_at > COMMENT_TTL:
        return None
    lane = _lane(roles)
    audio = db.get(AudioFile, wait.audio_id)
    book, ch = pair_of(db, audio) if audio is not None else (None, None)
    if not lane or user is None or book is None:
        return [send(chat_id, "The audition has been deleted; the comment was not saved.")]
    pair = {"book_id": book.id, "role": str(audio.role or ""), "actor_name": str(audio.actor_name or "")}
    try:
        set_comment(db, **pair, lane=lane, comment=text, voter_uid=str(user.id), voter_name=str(user.display_name or ""))
    except ScoreError as error:
        if error.code == "comment_too_long":
            return [send(chat_id, f"The comment is too long. Use at most {COMMENT_MAX_CHARS} characters.")]
        return [send(chat_id, "Rate the audition first; comments are attached to a rating.")]
    _audit(db, audio.id, "bot_audition_comment", user, now, lane=lane)
    role_title = str(ch.name if ch is not None else audio.role or "")
    out = [send(chat_id, "Saved. Only the studio will see this.")]
    return out + (_owner_notice(db, tid=tid, pair=pair, role_title=role_title) if lane == "author" else [])


def _lane(roles: set[str]) -> str:
    """Автор главный, как в голосах: у носителя обеих ролей — шкала автора."""
    return "author" if "author" in roles else "studio" if "admin" in roles else ""


def _audit(db, audio_id: str, action: str, user, now, **extra) -> None:
    import json
    from app.models import AuditLog

    db.add(AuditLog(user_id=str(getattr(user, "id", "") or ""), entity_type="audio_file", entity_id=audio_id,
                    action=action, created_at=now, payload_json=json.dumps(extra, ensure_ascii=False)))


def press(db, *, chat_id, message_id, data: str, user, roles: set[str], now, tid: str = "") -> list:
    from sqlalchemy.exc import IntegrityError

    from app.services.audition_rejections import sync_rejection_with_scores
    from app.services.audition_scores import REASON_MAX_SCORE, ScoreError, set_score
    from app.services.bot_out import Outgoing, send

    lane = _lane(roles)
    if not lane or user is None:
        return []
    action, _, audio_id = data[len(AC_PREFIX):].partition(":")
    audio = db.get(AudioFile, audio_id)
    if audio is None:
        return [send(chat_id, "Audition deleted.")]
    book, ch = pair_of(db, audio)
    if book is None:
        return [send(chat_id, "Audition book not found.")]
    pair = {"book_id": book.id, "role": str(audio.role or ""), "actor_name": str(audio.actor_name or "")}
    role_title = str(ch.name if ch is not None else audio.role or "")
    tid = str(tid or chat_id or "")  # в личке id чата и есть id человека

    def already_approved() -> bool:
        from app.services.role_deadlines import same_person
        from app.v2.cast_ops import parse_actor_name

        holder, tentative = parse_actor_name(str(getattr(ch, "actor_name", "") or ""))
        return bool(holder) and not tentative and same_person(holder, str(audio.actor_name or ""))

    def refreshed(approve: bool | None = None) -> Outgoing:
        # после оценки «Утвердить» не возвращается на уже утверждённую карточку (ревью C1)
        approve = (not already_approved()) if approve is None else approve
        return Outgoing("editMessageCaption", {"chat_id": chat_id, "message_id": message_id,
                                               "caption": caption(db, audio), "reply_markup": keyboard(audio.id, approve=approve)})

    if action.startswith("s") and action[1:].isascii() and action[1:].isdigit():
        score = int(action[1:])
        try:
            set_score(db, **pair, lane=lane, score=score, voter_uid=str(user.id),
                      voter_name=str(user.display_name or ""))
        except ScoreError:
            return [send(chat_id, "The rating cannot be saved because this audition no longer exists.")]
        # Оба ниже 7 — отказ через 15 минут, как на сайте; одновременное нажатие второго — повтор.
        try:
            sync_rejection_with_scores(db, **pair)
            db.commit()
        except IntegrityError:
            db.rollback()
            sync_rejection_with_scores(db, **pair)
            db.commit()
        _audit(db, audio.id, "bot_audition_score", user, now, lane=lane, score=score)
        out = [refreshed()]
        if lane == "author":
            low = score <= REASON_MAX_SCORE
            out.append(send(chat_id, WHY_LOW if low else WHY_HIGH, ask_keyboard(audio.id, reasons=low)))
        return out
    if action == "c":
        current = scores_by_pair(db, book.id).get((pair["role"], pair["actor_name"]), {}).get(lane)
        if not current:
            return [send(chat_id, "Rate the audition first; comments are attached to a rating.")]
        drop_comment_wait(db, tid)
        db.add(BotCommentWait(tid=tid, audio_id=audio.id, created_at=now))
        db.flush()
        prompt = (f"Describe in one message how the character «{role_title}» should sound. "
                  "Only the studio will see this; it will not be sent to the actor.")
        return [send(chat_id, prompt, {"force_reply": True, "input_field_placeholder": "For example: brighter, younger"})]
    if action.startswith("r") and action[1:] in REASON_LABELS:
        if lane != "author":
            return []
        current = scores_by_pair(db, book.id).get((pair["role"], pair["actor_name"]), {}).get("author")
        if not current or int(current["score"]) > REASON_MAX_SCORE:
            return [send(chat_id, "A reason is only needed for a rating below 7.")]
        set_score(db, **pair, lane="author", score=int(current["score"]), voter_uid=str(user.id),
                  voter_name=str(user.display_name or ""), reason=action[1:])
        out = [send(chat_id, f"Saved: {REASON_LABELS[action[1:]].lower()}. "
                             f"Describe how the character should sound with «{COMMENT_BUTTON}».")]
        return out + _owner_notice(db, tid=tid, pair=pair, role_title=role_title)
    if action in ("a", "A", "x"):
        return _approve(db, chat_id=chat_id, message_id=message_id, audio=audio, character=ch, user=user,
                        roles=roles, confirmed=action == "A", declined=action == "x", now=now, refreshed=refreshed)
    return []


def _approve(db, *, chat_id, message_id, audio, character, user, roles, confirmed, declined, now, refreshed) -> list:
    from app.services.bot_out import send
    from app.services.cast_assign import assign_actor
    from app.services.role_deadlines import same_person
    from app.v2.cast_ops import parse_actor_name

    if declined:
        return [send(chat_id, "No approval has been made.")]
    if character is None:
        return [send(chat_id, "Role not found in the cast.")]
    actor = str(audio.actor_name or "").strip()
    holder, tentative = parse_actor_name(str(character.actor_name or ""))
    if holder and not tentative and same_person(holder, actor):
        return [send(chat_id, f"The role is already approved: {holder}.")]
    if not confirmed:
        busy = sorted(
            str(other.name or "") for other in db.query(Character).filter(Character.book_id == character.book_id)
            if other.id != character.id and (lambda p: p[0] and not p[1] and same_person(p[0], actor))(
                parse_actor_name(str(other.actor_name or "")))
        )
        if busy:
            ask = {"inline_keyboard": [[{"text": "Yes", "callback_data": f"{AC_PREFIX}A:{audio.id}"},
                                        {"text": "No", "callback_data": f"{AC_PREFIX}x:{audio.id}"}]]}
            on = ", ".join(f"«{name}»" for name in busy)
            return [send(chat_id, f"For {on}, {actor} is already assigned. Also approve for «{character.name}»? "
                                  "We aim for one role per actor.", ask)]
    result = assign_actor(db, character=character, actor_name=actor, voter_uid=str(user.id),
                          voter_name=str(user.display_name or ""), roles=roles)
    _audit(db, audio.id, "bot_audition_approve", user, now, character_id=character.id)
    db.flush()
    vote = result["vote"]
    won, _ = parse_actor_name(str(vote["actor_name"] or ""))
    if not (won and same_person(won, actor)):
        return [send(chat_id, f"Not approved: the deciding vote belongs to {vote.get('overridden_by') or 'another participant'}.")]
    return [refreshed(approve=False), send(chat_id, f"Approved: {actor} — «{character.name}».")]
