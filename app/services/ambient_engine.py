"""Эмбиент по главе: по уникальному инструментальному треку на каждую сцену.

Промпт пишет модель шага «ambient_text» (`routed_ask` консилиума), музыку — ElevenLabs (`compose_music`). Трек
ложится файлом в папку главы рядом с дублями (`AudioFile(kind='ambient')`) и строкой
`ambient_tracks`; каждый сохраняется сразу — оборвавшийся прогон продолжается со сцен без
готового трека, готовое повторно не оплачивается. Строка хода — `BackgroundRun`, очередь —
`consilium`, как у звуковой разметки (`sound_engine`). Правила — в `app.pipeline.ambient`.
"""
from __future__ import annotations

import logging
import re
from typing import Callable

from app.services.consilium_engine import STOP_TEXT, _finish, checkpoint_run
from app.services.audio_mirror import LocalDiskFull
from app.services.elevenlabs_client import (
    BadKey,
    ElevenLabsError,
    ElevenLabsInterrupted,
    ElevenLabsUnavailable,
    QuotaExhausted,
    compose_music,
)

logger = logging.getLogger(__name__)

KIND = "ambient"
#: модель по умолчанию; настоящая берётся из шага «ambient_text» в начале прогона
MODEL = "anthropic/claude-opus-5"
ATTEMPTS = 2
#: сколько прежних треков книги видит Opus — чтобы не повторять их инструменты и фактуру
PREVIOUS_LIMIT = 40
#: сколько знаков начала сцены уходит в промпт
EXCERPT_CHARS = 1500
ROLE = "Эмбиент"
MIME = "audio/mpeg"
MAX_NAME_ATTEMPTS = 100
QUOTA_TEXT = "квота исчерпана"
BAD_KEY_TEXT = "Ключ ElevenLabs отклонён"
INTERRUPTED_TEXT = "ответ ElevenLabs оборвался — трек мог быть оплачен; проверьте историю в ElevenLabs"
DISK_TEXT = "не хватило места на диске"
STORE_FAILED_TEXT = f"{DISK_TEXT} — трек оплачен, но не сохранён"
NOT_FOUND_TEXT = "сцена не найдена среди активных сцен главы"
#: mp3 192 кбит/с — верхняя граница размера трека до его оплаты
MP3_BYTES_PER_SECOND = 192000 // 8


class SceneNotFound(Exception):
    """Перегенерация сцены, которой нет среди активных сцен главы (сняли, потеряли, чужая)."""


class DiskFull(Exception):
    """Места на диске нет ещё до оплаты трека — прогон останавливается, ничего не потратив."""


def _chapter_scenes(db, chapter_id: str) -> list[dict]:
    """Все активные сцены главы по порядку: имя файла, разметка, отрезок на таймлайне (или None)."""
    from app.models import ScriptChapter
    from app.pipeline.ambient import NO_PLACE, ambient_file_names, track_seconds
    from app.services.chapter_delivery import active_scene_rows, scene_spans
    from app.services.sound_store import _ordinal

    chapter = db.get(ScriptChapter, str(chapter_id or ""))
    if chapter is None:
        return []
    scenes, places = active_scene_rows(db, chapter.id)
    if not scenes:
        return []
    spans = scene_spans(db, chapter.id)
    place_of = [places.get(row["place_id"]) if row.get("place_id") else None for row in scenes]
    names = ambient_file_names(int(chapter.chapter_index or 0),
                               [place.name if place is not None else NO_PLACE for place in place_of])
    out = []
    for row, place, name in zip(scenes, place_of, names):
        payload = row["payload"] or {}
        span = spans.get(row["id"])
        out.append({
            "marker_id": row["id"],
            "ordinal": _ordinal(row["segment_id"]),
            "file_name": name,
            "span": span,
            "seconds": track_seconds(span[1]) if span is not None else 0,
            "scene": {
                "place": {"name": place.name if place is not None else "",
                          "description": place.description if place is not None else ""},
                "time_of_day": payload.get("time_of_day") or "",
                "weather": payload.get("weather") or "",
                "ambience": payload.get("ambience") or "",
                "mood": payload.get("mood") or "",
                "music_queries": list(payload.get("music_queries") or []),
            },
        })
    return out


def done_tracks(db, *columns):
    """Выборка готовых треков — одно определение «готового» на всех: строка `ambient_tracks`
    со `status='done'` И живая строка `AudioFile(kind='ambient')` под ней. Трек, чей файл
    стёрли, готовым не считается: сцена снова ждёт трека, в проект и архив он не ложится.

    `columns` — что выбирать (по умолчанию `AmbientTrack`); можно и `AudioFile`. Фильтры по
    главе и прочему добавляет вызывающий.
    """
    from app.models import AmbientTrack, AudioFile

    return (db.query(*(columns or (AmbientTrack,)))
            .select_from(AmbientTrack)
            .join(AudioFile, AudioFile.id == AmbientTrack.audio_file_id)
            .filter(AmbientTrack.status == "done", AudioFile.kind == KIND))


def _done_markers(db, chapter_id: str) -> set[str]:
    from app.models import AmbientTrack

    return {mid for (mid,) in done_tracks(db, AmbientTrack.marker_id)
            .filter(AmbientTrack.chapter_id == chapter_id)}


def _public(item: dict) -> dict:
    return {key: item[key] for key in ("marker_id", "seconds", "file_name", "scene")}


def ambient_plan(db, chapter_id: str) -> list[dict]:
    """Сцены главы, которым ещё нужен трек: на таймлайне есть место, готового (`done`) трека
    нет. `{marker_id, seconds, file_name, scene}` — для строки ASR и сметы."""
    done = _done_markers(db, chapter_id)
    return [_public(item) for item in _chapter_scenes(db, chapter_id)
            if item["span"] is not None and item["marker_id"] not in done]


def _previous_summaries(db, book_id: str) -> list[str]:
    """Краткие описания последних треков книги (готовых и заменённых), от старых к новым."""
    from app.models import AmbientTrack

    rows = (db.query(AmbientTrack.summary)
            .filter(AmbientTrack.book_id == book_id, AmbientTrack.status.in_(("done", "replaced")),
                    AmbientTrack.summary != "")
            .order_by(AmbientTrack.created_at.desc(), AmbientTrack.id.desc())
            .limit(PREVIOUS_LIMIT).all())
    return [summary for (summary,) in reversed(rows)]


def _excerpt(db, chapter_id: str, start: int, stop: int | None) -> str:
    """Начало текста сцены: абзацы от её начала до следующей сцены, не длиннее ~1500 знаков."""
    from app.v2.models import V2Segment

    query = db.query(V2Segment).filter(V2Segment.chapter_id == chapter_id, V2Segment.ordinal >= start)
    if stop is not None:
        query = query.filter(V2Segment.ordinal < stop)
    parts: list[str] = []
    length = 0
    for segment in query.order_by(V2Segment.ordinal.asc()):
        text = str(segment.text or "").strip()
        if not text:
            continue
        parts.append(text)
        length += len(text) + 1
        if length >= EXCERPT_CHARS:
            break
    return "\n".join(parts)[:EXCERPT_CHARS]


def _free_key(db, folder_key: str, file_name: str) -> tuple[str, str]:
    """Ключ и имя файла, не занятые ни строкой, ни файлом на диске. Занято (перегенерация
    той же сцены) — не затираем, а берём « (2)», « (3)»… перед расширением."""
    from app.models import AudioFile
    from app.services import audio_storage

    stem = re.sub(r"\.mp3$", "", file_name)
    name = file_name
    for number in range(2, MAX_NAME_ATTEMPTS + 2):
        key = f"{folder_key}/{name}"
        has_row = db.query(AudioFile.id).filter(AudioFile.stored_key == key).first() is not None
        on_disk = audio_storage.file_exists(key)
        if not has_row and not on_disk:
            return key, name
        if on_disk and not has_row:
            # Сирота: файл записан, а строка не закоммичена (прогон оборвался между ними).
            # Не затираем — вдруг это оплаченный трек, — но и не молчим.
            logger.warning("ambient: на диске файл без строки AudioFile, беру другое имя: %s", key)
        name = f"{stem} ({number}).mp3"
    raise RuntimeError(f"storage_key_exhausted: {file_name}")


def _ensure_room(size_hint: int) -> None:
    from app.config import settings
    from app.services import audio_mirror, audio_storage

    audio_mirror.ensure_room(
        size_hint,
        free=audio_storage.free_bytes(audio_storage.local_root()),
        reserve_bytes=int(settings.local_reserve_gb) * 1024**3,
    )


def _store_track(db, chapter, book, *, file_name: str, data: bytes, seconds: int):
    """Записать mp3 в папку главы и завести `AudioFile(kind='ambient')`. Коммит — у вызывающего."""
    from app.models import AudioFile
    from app.services import audio_storage
    from app.services.audio_mirror import chapter_folder
    from app.services.audio_naming import book_token
    from app.services.audio_probe import probe_audio_file
    from app.services.audio_uploads import derive_book_code
    from app.services.chapter_delivery import _chapter_label

    book_code = derive_book_code(book.title if book is not None else "")
    label = _chapter_label(chapter)
    folder_key = f"{book_token(book_code) or 'BOOK'}/{chapter_folder(label)}"
    key, name = _free_key(db, folder_key, file_name)
    _ensure_room(len(data))
    digest = audio_storage.write_file(key, data)
    probe = probe_audio_file(audio_storage.resolve_path(key))
    item = AudioFile(
        book_code=book_code,
        original_filename=name,
        canonical_filename=name,
        stored_key=key,
        mime_type=MIME,
        size_bytes=len(data),
        md5=digest,
        chapter=label,
        role=ROLE,
        actor_name="",
        kind=KIND,
        location="local",
        status="uploaded",
        duration_seconds=float(probe.get("duration_seconds") or seconds),
        sample_rate=int(probe.get("sample_rate") or 0),
        channels=int(probe.get("channels") or 0),
        codec=str(probe.get("codec") or ""),
    )
    db.add(item)
    db.flush()
    return item


def enqueue_ambient(chapter_id: str, marker_id: str = "", prompt_override: str = "") -> str | None:
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    return enqueue_tracked_task(
        queue_name="consilium",
        func_ref="app.worker_tasks.perform_ambient_task",
        session_factory=SessionLocal,
        background_run_cls=BackgroundRun,
        job_kind=KIND,
        entity_type="script_chapter",
        entity_id=chapter_id,
        run_key=f"{KIND}:{chapter_id}",
        meta={"phase": "queued", "chapter_id": chapter_id, "marker_id": marker_id},
        chapter_id=chapter_id,
        marker_id=marker_id,
        prompt_override=prompt_override,
    )


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def run_ambient(*, session_factory, chapter_id: str, run_id: str, marker_id: str = "",
                prompt_override: str = "", ask=None, compose=None,
                notify: Callable[[str], None] | None = None) -> dict:
    from app.models import AmbientTrack, ScriptBook, ScriptChapter
    from app.pipeline.ambient import PROMPT_SCHEMA, PROMPT_SYSTEM, parse_prompt, prompt_user
    from app.pipeline.consilium_run import StopRun
    from app.services.sound_store import touch_sessions
    from app.time_utils import utcnow_naive

    # Перегенерация одной сцены, не дошедшая до строки трека (квота, ключ, диск, стоп, сцены
    # нет), всё равно пишет сцене `failed` с причиной: иначе карточка ждала бы «сочиняется»
    # вечно — её снимок трека не меняется.
    scene = {"book_id": "", "prompt": "", "seconds": 0, "settled": not marker_id}

    def settle_scene(text: str) -> None:
        if scene["settled"] or not scene["book_id"]:
            return
        scene["settled"] = True
        try:
            with session_factory() as db:
                now = utcnow_naive()
                db.add(AmbientTrack(book_id=scene["book_id"], chapter_id=chapter_id, marker_id=marker_id,
                                    prompt=scene["prompt"], duration_seconds=scene["seconds"],
                                    status="failed", error=text, created_at=now, updated_at=now))
                db.commit()
        except Exception:  # noqa: BLE001 — строка для карточки не валит прогон
            logger.exception("ambient: не записал сбой сцены %s", marker_id)

    meta = {"phase": "tracks", "chapter_id": chapter_id, "marker_id": marker_id, "tracks_total": 0,
            "tracks_done": 0, "tracks_failed": 0, "tracks_skipped": 0}
    result = {"status": "done", "reason": "", "chapter_index": 0, "tracks_total": 0, "tracks_done": 0,
              "tracks_failed": 0, "tracks_skipped": 0}

    def sync() -> None:
        for name in ("tracks_total", "tracks_done", "tracks_failed", "tracks_skipped"):
            result[name] = meta[name]

    def checkpoint() -> None:
        if checkpoint_run(session_factory, run_id, meta):
            raise StopRun("stopped_by_user")

    from app.services.consilium_engine import routed_ask
    from app.services.step_models import MissingKeyError, require_key_for, step_model

    text_provider, text_model = step_model("ambient_text")  # один раз на прогон
    audio_model = step_model("ambient_audio")[1]
    real_audio = compose is None
    if real_audio:
        # Модель звука — та, что была на старте: смена на странице посреди главы не делает
        # треки одной главы разными моделями.
        def compose(prompt, seconds):
            return compose_music(prompt, seconds, model_id=audio_model)
    real_calls = ask is None  # подменный ask в тестах не тратит деньги — ключ ему не нужен
    if real_calls:
        ask = routed_ask({text_model: text_provider})

    try:
        # Ключи — до первой траты: без ключа звука оплаченный текст сцены ушёл бы впустую.
        # Текстовый ключ не нужен, когда промпт сцены уже дан (перегенерация с правкой).
        needed = (["ambient_audio"] if real_audio else []) + (["ambient_text"] if real_calls and not prompt_override else [])
        for step in needed:
            try:
                require_key_for(step)
            except MissingKeyError as err:
                raise StopRun(str(err))
        with session_factory() as db:
            chapter = db.get(ScriptChapter, str(chapter_id or ""))
            if chapter is None:
                raise LookupError(f"глава {chapter_id} не найдена")
            book_id = str(chapter.book_id)
            scene["book_id"] = book_id
            result["chapter_index"] = int(chapter.chapter_index or 0)
            all_scenes = _chapter_scenes(db, chapter.id)
            scenes = all_scenes
            if marker_id:
                scenes = [item for item in scenes if item["marker_id"] == marker_id]
                if not scenes:
                    raise SceneNotFound(NOT_FOUND_TEXT)
            else:
                done = _done_markers(db, chapter.id)
                scenes = [item for item in scenes if item["marker_id"] not in done]
        # Сцена без места на таймлайне в проект не ляжет — генерировать её не за что платить.
        todo = [item for item in scenes if item["span"] is not None]
        meta.update(tracks_total=len(todo), tracks_skipped=len(scenes) - len(todo))
        sync()
        checkpoint()
        ordinals = sorted(scene["ordinal"] for scene in all_scenes)

        for item in todo:
            with session_factory() as db:
                following = [o for o in ordinals if o > item["ordinal"]]
                excerpt = _excerpt(db, chapter_id, item["ordinal"], following[0] if following else None)
                previous = _previous_summaries(db, book_id)
                prior = (db.query(AmbientTrack)
                         .filter(AmbientTrack.chapter_id == chapter_id, AmbientTrack.marker_id == item["marker_id"],
                                 AmbientTrack.status == "done")
                         .order_by(AmbientTrack.created_at.desc()).first())
                prior_summary = prior.summary if prior is not None else ""

            prompt, summary = "", ""
            scene.update(seconds=item["seconds"], prompt=prompt_override.strip() if marker_id else "")
            if marker_id and prompt_override.strip():
                # Правленый промпт из карточки идёт в ElevenLabs как есть; описание для
                # следующих вызовов — прежнее этой сцены, а нет его — сам промпт.
                prompt = prompt_override.strip()
                summary = prior_summary or prompt[:200]
            # До оплаты: хватит ли места под трек по верхней границе. Нет — стоп, ничего не
            # потрачено; оплаченный и не записанный трек — это деньги в никуда.
            try:
                _ensure_room(item["seconds"] * MP3_BYTES_PER_SECOND)
            except LocalDiskFull as exc:
                raise DiskFull(DISK_TEXT) from exc
            data, song_id, error = None, "", ""
            # Повторяется только то, что не могло стоить денег: сбой Opus/разбора промпта,
            # запрос, не ушедший в ElevenLabs, и 5xx. Оборванный ответ мог быть оплачен —
            # второй запрос оплатил бы трек дважды; прочий отказ ElevenLabs не пройдёт и снова.
            for _attempt in range(ATTEMPTS):
                try:
                    if not prompt:
                        answer = ask(text_model, PROMPT_SYSTEM,
                                     prompt_user(item["scene"], excerpt, item["seconds"], previous), PROMPT_SCHEMA)
                        prompt, summary = parse_prompt(answer)
                        scene["prompt"] = prompt
                    data, song_id = compose(prompt, item["seconds"])
                    break
                except (QuotaExhausted, BadKey):
                    raise
                except ElevenLabsInterrupted as exc:
                    error = INTERRUPTED_TEXT
                    logger.warning("ambient: сцена %s: %s (%s)", item["marker_id"], INTERRUPTED_TEXT, _short(exc))
                    break
                except ElevenLabsError as exc:
                    error = _short(exc)
                    logger.warning("ambient: трек сцены %s не вышел: %s", item["marker_id"], error)
                    if not isinstance(exc, ElevenLabsUnavailable):
                        break
                except Exception as exc:  # noqa: BLE001 — сбой одного трека не валит главу
                    error = _short(exc)
                    logger.warning("ambient: трек сцены %s не вышел: %s", item["marker_id"], error)

            with session_factory() as db:
                chapter = db.get(ScriptChapter, chapter_id)
                book = db.get(ScriptBook, book_id)
                now = utcnow_naive()
                if data is None:
                    db.add(AmbientTrack(book_id=book_id, chapter_id=chapter_id, marker_id=item["marker_id"],
                                        prompt=prompt, summary=summary, duration_seconds=item["seconds"],
                                        status="failed", error=error, created_at=now, updated_at=now))
                    db.commit()
                    scene["settled"] = True
                    meta["tracks_failed"] += 1
                else:
                    try:
                        audio = _store_track(db, chapter, book, file_name=item["file_name"], data=data,
                                             seconds=item["seconds"])
                    except Exception as exc:  # noqa: BLE001 — трек оплачен: сбой записи не валит главу
                        db.rollback()
                        reason = STORE_FAILED_TEXT if isinstance(exc, (LocalDiskFull, OSError)) \
                            else f"трек оплачен, но не сохранён: {_short(exc)}"
                        logger.error("ambient: %s (сцена %s, song_id %s)", reason, item["marker_id"], song_id,
                                     exc_info=True)
                        db.add(AmbientTrack(book_id=book_id, chapter_id=chapter_id, marker_id=item["marker_id"],
                                            prompt=prompt, summary=summary, duration_seconds=item["seconds"],
                                            song_id=song_id, status="failed", error=reason,
                                            created_at=now, updated_at=now))
                        db.commit()
                        scene["settled"] = True
                        meta["tracks_failed"] += 1
                        sync()
                        checkpoint()
                        continue
                    for old in (db.query(AmbientTrack)
                                .filter(AmbientTrack.chapter_id == chapter_id,
                                        AmbientTrack.marker_id == item["marker_id"], AmbientTrack.status == "done")):
                        old.status, old.updated_at = "replaced", now
                    db.add(AmbientTrack(book_id=book_id, chapter_id=chapter_id, marker_id=item["marker_id"],
                                        audio_file_id=audio.id, prompt=prompt, summary=summary,
                                        duration_seconds=item["seconds"], song_id=song_id, status="done",
                                        created_at=now, updated_at=now))
                    touch_sessions(db, [chapter_id])
                    db.commit()
                    scene["settled"] = True
                    meta["tracks_done"] += 1
            sync()
            checkpoint()

        meta.update(phase="done", result=result)
        _finish(session_factory, run_id, "done", meta, "")
    except QuotaExhausted as exc:
        # Квота — не сбой прогона: готовое остаётся, строка показывает «квота исчерпана».
        sync()
        settle_scene(QUOTA_TEXT)
        result.update(status="quota", reason=_short(exc))
        meta.update(phase="done", result=result)
        _finish(session_factory, run_id, "done", meta, QUOTA_TEXT)
    except BadKey as exc:
        # Ключ отклонён — как квота: дальше каждый трек получит тот же отказ, прогон стоит.
        sync()
        settle_scene(BAD_KEY_TEXT)
        result.update(status="bad_key", reason=_short(exc))
        meta.update(phase="done", result=result)
        _finish(session_factory, run_id, "done", meta, BAD_KEY_TEXT)
    except SceneNotFound:
        settle_scene(NOT_FOUND_TEXT)
        result.update(status="not_found", reason=NOT_FOUND_TEXT)
        meta.update(phase="done", result=result)
        _finish(session_factory, run_id, "done", meta, NOT_FOUND_TEXT)
    except DiskFull:
        sync()
        settle_scene(DISK_TEXT)
        result.update(status="disk_full", reason=DISK_TEXT)
        meta.update(phase="done", result=result)
        _finish(session_factory, run_id, "done", meta, DISK_TEXT)
    except StopRun as stop:
        sync()
        settle_scene(STOP_TEXT.get(stop.reason, stop.reason))
        result.update(status="stopped", reason=stop.reason)
        meta.update(phase="stopped", result=result)
        _finish(session_factory, run_id, "stopped", meta, STOP_TEXT.get(stop.reason, stop.reason))
    except Exception as exc:  # noqa: BLE001
        sync()
        settle_scene(f"прогон упал: {_short(exc)}")
        result.update(status="failed", reason=_short(exc))
        meta.update(phase="failed", result=result)
        _finish(session_factory, run_id, "failed", meta, result["reason"])
        _notify(session_factory, notify, result)
        raise
    _notify(session_factory, notify, result)
    return result


def notify_text(result: dict) -> str:
    head = f"Г{result.get('chapter_index', 0)}: эмбиент"
    if result["status"] == "failed":
        return f"{head} упал: {result['reason']}"
    if result["status"] == "not_found":
        return f"{head}: {NOT_FOUND_TEXT}"
    text = f"{head} {result['tracks_done']} из {result['tracks_total']}"
    if result["tracks_failed"]:
        text += f", ошибок {result['tracks_failed']}"
    if result["status"] == "quota":
        text += f", {QUOTA_TEXT}"
    elif result["status"] == "bad_key":
        text += f", {BAD_KEY_TEXT[0].lower()}{BAD_KEY_TEXT[1:]}"
    elif result["status"] == "disk_full":
        text += f", {DISK_TEXT}"
    elif result["status"] == "stopped":
        text += f", {STOP_TEXT.get(result['reason'], result['reason'])}"
    if result["tracks_skipped"]:
        text += f", сцен без места на таймлайне {result['tracks_skipped']}"
    return text


def _notify(session_factory, notify, result: dict) -> None:
    text = notify_text(result)
    try:
        if notify is not None:
            notify(text)
        else:
            from app.services.telegram import send_telegram_message
            with session_factory() as db:
                send_telegram_message(db, text)
    except Exception:  # noqa: BLE001 — уведомление не валит прогон
        logger.exception("Итог генерации эмбиента не отправлен в Telegram: %s", text[:120])
