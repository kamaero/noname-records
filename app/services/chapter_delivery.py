"""Глава записана — и её пора отдавать в монтаж.

Готовность до сих пор считалась через распознавание речи: сколько реплик сценария
нашлось в аудио. Распознавание не запускалось ни разу, поэтому готовность всегда
показывала ноль, и вопрос «эту главу уже можно сводить?» оставался без ответа.

Ответ дешевле, чем казалось: у каждой говорящей роли главы либо есть дубль, либо нет.
Это не то же самое, что «все реплики произнесены» — этого без распознавания не узнать, —
но это то, что можно узнать сегодня, не потратив ни секунды машинного времени.

Пробы в счёт не идут: проба — заявка на роль, а не запись главы.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from app.time_utils import iso_utc, utcnow_naive

from app.models import AsrJob, AudioFile, Character, ScriptBook, ScriptChapter
from app.services import audio_storage, nas_health
from app.services.audio_integrity import MISMATCH, MISSING, OK
from app.services.audio_mirror import MISMATCH as MIRROR_MISMATCH
from app.services.audio_naming import book_token, chapter_token
from app.services.audio_uploads import TAKE, derive_book_code
from app.services.audition_session import SessionClip, SessionMarker, SessionTrack, build_session_xml
from app.services.shared_runtime import SERVER_TIMEZONE
from app.services.asr_run import chapter_replicas
from app.services.sound_store import _ordinal as _marker_ordinal
from app.services.sound_store import active_chapter_markers
from app.services.telegram import send_telegram_message
from app.v2.cast_ops import chapter_role_counts, is_placeholder_name
from app.v2.reader import effective_book_attributions
from app.v2.models import V2Segment

logger = logging.getLogger(__name__)


def _chapter_label(chapter: ScriptChapter) -> str:
    """Под каким именем глава записана в файлах — тем, что видел диктор в форме."""
    return str(chapter.chapter_title or "").strip() or f"Глава {int(chapter.chapter_index or 0)}"


def book_role_counts_by_chapter(db, book_id: str) -> dict[str, dict[str, int]]:
    """Кто сколько говорит в каждой главе книги — одним проходом.

    Поглавно это те же данные, что даёт `chapter_role_counts`, но шестьдесят вызовов —
    это шестьдесят загрузок сегментов и атрибуций. Книга читается один раз.
    """
    chapter_of = {
        str(segment_id): str(chapter_id)
        for segment_id, chapter_id in db.query(V2Segment.id, V2Segment.chapter_id).filter(V2Segment.book_id == book_id).all()
    }
    counts: dict[str, dict[str, int]] = {}
    # реплика — абзац, где роль говорит, как в `chapter_role_counts`
    counted: set[tuple[str, str]] = set()
    for segment_id, _start, _end, speaker, _source in effective_book_attributions(db, book_id):
        name = str(speaker or "").strip()
        if not name or is_placeholder_name(name):
            continue
        chapter = chapter_of.get(str(segment_id), "")
        if not chapter or (name, str(segment_id)) in counted:
            continue
        counted.add((name, str(segment_id)))
        counts.setdefault(chapter, {})
        counts[chapter][name] = counts[chapter].get(name, 0) + 1
    return counts


def book_recording_status(db, book_id: str, *, ambient: bool = False) -> dict | None:
    """Каждая опубликованная глава книги: сколько ролей записано и готова ли она.

    `ambient` — посчитать и поля эмбиента (`ambient_*`, см. `_ambient_fields`): только для
    редактора — дикторам эмбиент не показан. Фоновая архивация их не просит; без флага
    поля нулевые.
    """
    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        return None
    chapters = (
        db.query(ScriptChapter)
        .filter(ScriptChapter.book_id == book.id)
        .order_by(ScriptChapter.chapter_index.asc())
        .all()
    )
    counts = book_role_counts_by_chapter(db, book.id)
    labels = {_chapter_label(chapter): chapter for chapter in chapters}
    recorded: dict[str, set[str]] = {}
    audio_of_chapter: dict[str, list[str]] = {}
    role_of_audio: dict[str, str] = {}
    ok_files: dict[str, int] = {}
    bad_files: dict[str, int] = {}
    all_files: dict[str, int] = {}
    unverifiable: dict[str, int] = {}
    mirrored: dict[str, int] = {}
    unmirrored: dict[str, int] = {}
    stale: dict[str, int] = {}
    # Расхождение сумм отдельно от «просто ждёт»: у первого копия на NAS битая, и
    # круг зеркалирования его не возьмёт больше никогда, а второй приедет сам.
    # Человеку это разные новости — в одном случае надо вмешаться, в другом ждать.
    mirror_broken: dict[str, int] = {}
    # Порог «дольше суток»: считаем от загрузки, не от текущего момента внутри цикла —
    # иначе тысяча файлов книги проверялась бы по тысяче чуть разных мгновений.
    stale_cutoff = utcnow_naive() - timedelta(days=1)
    rows_of_files = db.query(
        AudioFile.id, AudioFile.chapter, AudioFile.role, AudioFile.verify_state, AudioFile.md5,
        AudioFile.mirrored_at, AudioFile.mirror_state, AudioFile.uploaded_at,
    ).filter(AudioFile.kind == TAKE).all()
    for audio_id, label, role, verify_state, md5, mirrored_at, mirror_state, uploaded_at in rows_of_files:
        chapter = labels.get(str(label or "").strip())
        if chapter is None:
            continue
        recorded.setdefault(chapter.id, set()).add(str(role or "").strip())
        audio_of_chapter.setdefault(chapter.id, []).append(str(audio_id))
        role_of_audio[str(audio_id)] = str(role or "").strip()
        all_files[chapter.id] = all_files.get(chapter.id, 0) + 1
        state = str(verify_state or "")
        if state == OK:
            ok_files[chapter.id] = ok_files.get(chapter.id, 0) + 1
        elif state in {MISMATCH, MISSING}:
            bad_files[chapter.id] = bad_files.get(chapter.id, 0) + 1
        # Дубли без суммы вообще — сверять их не с чем совсем, и `verify_file` честно
        # отвечает `skipped`, не трогая `verify_state`. Пока это было не видно снаружи,
        # глава из одних таких файлов навсегда оставалась «непроверенной»: кнопка
        # «сверить» рисовалась вечно и запускала пустое задание. Записи старше миграции
        # 0020 сумму приёма не имеют — она у них появляется только через переезд в
        # новую раскладку (scripts/migrate_audio_layout.py), задним числом, и
        # доказывает только «файл не менялся с момента переезда», а не «файл такой,
        # каким его записал диктор» (см. AudioFile.md5); пока переезд для конкретной
        # записи не прошёл, она остаётся здесь. Прошёл (полностью или частично, для
        # части таких строк) — для ЭТОГО счётчика они СВЕРЯЕМЫ и уйдут отсюда в ok/bad
        # при первой же проверке.
        if not str(md5 or "").strip():
            unverifiable[chapter.id] = unverifiable.get(chapter.id, 0) + 1
        if mirrored_at is not None:
            mirrored[chapter.id] = mirrored.get(chapter.id, 0) + 1
        else:
            unmirrored[chapter.id] = unmirrored.get(chapter.id, 0) + 1
            # Расхождение суммы — приговор без срока давности: `audio_mirror.mirror_once`
            # исключает такой файл из кругов навсегда, и `mirrored_at` у него не
            # появится никогда. Ждать сутки, чтобы предупредить о нём, здесь
            # бессмысленно — иначе «не на NAS» читалось бы как «подожди», а свежий
            # mismatch не изменится, сколько ни жди.
            if str(mirror_state or "") == MIRROR_MISMATCH:
                mirror_broken[chapter.id] = mirror_broken.get(chapter.id, 0) + 1
            if str(mirror_state or "") == MIRROR_MISMATCH or uploaded_at < stale_cutoff:
                stale[chapter.id] = stale.get(chapter.id, 0) + 1
    heard = _recognised_by_audio(db, [audio_id for ids in audio_of_chapter.values() for audio_id in ids])

    rows = []
    for chapter in chapters:
        roles = counts.get(chapter.id, {})
        have = recorded.get(chapter.id, set())
        done = sum(1 for role in roles if role in have)
        seen = [heard[audio_id] for audio_id in audio_of_chapter.get(chapter.id, []) if audio_id in heard]
        # По роли — лучший её файл, как в `chapter_recording_status`, а не сумма файлов:
        # у роли из нескольких дублей строки одного файла скопированы в остальные
        # (`asr_borrow._copy_from_siblings`), и сумма посчитала бы реплику дважды.
        best_of_role: dict[str, dict] = {}
        for audio_id in audio_of_chapter.get(chapter.id, []):
            entry = heard.get(audio_id)
            if entry is None:
                continue
            role = role_of_audio.get(audio_id, "")
            if role not in best_of_role or entry["coverage"] > best_of_role[role]["coverage"]:
                best_of_role[role] = entry
        matched = sum(entry["matched"] for entry in best_of_role.values())
        missing = sum(entry["missing"] for entry in best_of_role.values())
        total_lines = sum(int(count) for count in roles.values())
        rows.append({
            "chapter_id": str(chapter.id),
            "chapter_index": int(chapter.chapter_index or 0),
            "chapter_title": str(chapter.chapter_title or ""),
            "total_roles": len(roles),
            "recorded_roles": done,
            "ready": bool(roles) and done == len(roles),
            "delivered_at": iso_utc(chapter.delivered_at) or "",
            # None — не считали. Ноль означал бы «ничего не произнесено».
            "asr_coverage": (matched / total_lines) if (seen and total_lines) else None,
            "asr_files": len(seen),
            "total_lines": total_lines,
            "matched_lines": matched,
            "missing_lines": missing,
            # «Файл есть» и «файл цел» — разные вещи: обрыв на середине даёт запись
            # в базе и обрезок на диске.
            "integrity_ok": ok_files.get(chapter.id, 0),
            "integrity_bad": bad_files.get(chapter.id, 0),
            "integrity_checked": bool(ok_files.get(chapter.id, 0) or bad_files.get(chapter.id, 0)),
            # Сколько дублей главы сверить совсем не с чем (суммы нет ни от приёма, ни
            # от переезда) и сколько их всего: экран отличает «ещё не сверяли» от
            # «сверять не с чем» только по этой паре. Легаси-строки (старше миграции
            # 0020) получают сумму через переезд в новую раскладку
            # (scripts/migrate_audio_layout.py), задним числом, не исходную от диктора
            # (см. AudioFile.md5); как только для конкретной записи это произошло, она
            # попадает в ok/bad, а не сюда — до тех пор остаётся здесь.
            "integrity_skipped": unverifiable.get(chapter.id, 0),
            "integrity_files": all_files.get(chapter.id, 0),
            # Зеркало: сколько копий доехало и сколько ждут дольше суток —
            # второе означает, что канал лежит давно, а не пять минут.
            "mirrored_files": mirrored.get(chapter.id, 0),
            "unmirrored_files": unmirrored.get(chapter.id, 0),
            "stale_unmirrored": stale.get(chapter.id, 0),
            "mirror_broken": mirror_broken.get(chapter.id, 0),
            "session_archived": bool(chapter.session_archived_at),
            "session_outdated": bool(chapter.session_archived_at and chapter.session_outdated_at),
            # Записано архивацией (`archive_chapter_session`), не посчитано здесь заново.
            "session_markers_skipped": int(chapter.session_markers_skipped or 0),
            **AMBIENT_EMPTY,
        })
    if ambient:
        fields = _ambient_fields(db, [row["chapter_id"] for row in rows],
                                 {row["chapter_id"] for row in rows if row["ready"]})
        for row in rows:
            row.update(fields.get(row["chapter_id"], {}))
    return {
        "chapters": rows,
        "total_chapters": len(rows),
        "ready_chapters": sum(1 for row in rows if row["ready"]),
        "started_chapters": sum(1 for row in rows if row["recorded_roles"]),
        "recognised_chapters": sum(1 for row in rows if row["asr_files"]),
        "delivered_chapters": sum(1 for row in rows if row["delivered_at"]),
        "missing_lines": sum(row["missing_lines"] for row in rows),
    }


#: поля эмбиента строки главы, пока их не считали (не редактор)
AMBIENT_EMPTY = {"ambient_todo": 0, "ambient_done": 0, "ambient_failed": 0, "ambient_skipped": 0,
                 "ambient_broken": 0, "ambient_state": ""}
#: так `json.dumps` пишет пустой `marker_id` в ход прогона — прогон по всей главе, а не
#: перегенерация одной сцены (`ambient_engine.enqueue_ambient` / `run_ambient`)
_CHAPTER_WIDE_META = '%"marker_id": ""%'


def _latest_runs(db, keys: list[str], *conditions) -> dict[str, object]:
    """Последний прогон на каждый `run_key` — подзапросом по `max(created_at)`, без загрузки
    всей истории прогонов."""
    from sqlalchemy import func

    from app.models import BackgroundRun

    if not keys:
        return {}
    newest = (db.query(BackgroundRun.run_key.label("key"), func.max(BackgroundRun.created_at).label("at"))
              .filter(BackgroundRun.run_key.in_(keys), *conditions)
              .group_by(BackgroundRun.run_key).subquery())
    out: dict[str, object] = {}
    for run in (db.query(BackgroundRun)
                .join(newest, (BackgroundRun.run_key == newest.c.key) & (BackgroundRun.created_at == newest.c.at))
                .filter(*conditions)
                .order_by(BackgroundRun.id.asc())):
        out[run.run_key] = run
    return out


def _run_meta(run) -> dict:
    try:
        meta = json.loads(run.meta_json or "{}")
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _ambient_fields(db, chapter_ids: list[str], ready: set[str]) -> dict[str, dict]:
    """Эмбиент в строке главы — дёшево, без раскладки по таймлайну: сколько активных сцен
    записанной главы ещё без готового трека, сколько с готовым, сколько треков не вышло в
    последнем прогоне, сколько сцен последний прогон по всей главе пропустил (нет места на
    таймлайне) и состояние. Длина треков — в смете (`GET /chapters/{id}/ambient/plan`), её
    зовёт только подтверждение запуска.

    `ambient_state`: `running` — последний прогон в очереди или идёт; иначе по его итогу
    `quota` | `bad_key` (ключ ElevenLabs отклонён) | `disk_full` | `failed` (упал сам или не вышел
    хоть один трек); `done` — глава
    записана, готовый трек есть и сцен без трека не больше, чем пропустил последний прогон по
    всей главе (сцены вне таймлайна генерировать не за что — «готово» и с ними); `''` — ничего
    из этого. Только чтение: молчащий дольше `STALE_RUN_MINUTES` прогон считается упавшим,
    но в базе не отмечается — это дело `expire_dead_runs` на ручках запуска.
    """
    from datetime import timedelta

    from sqlalchemy import func

    from app.models import AmbientTrack, BackgroundRun, SoundMarker
    from app.services.ambient_engine import done_tracks
    from app.services.consilium_engine import STALE_RUN_MINUTES

    if not chapter_ids:
        return {}
    scenes = dict(db.query(SoundMarker.chapter_id, func.count(SoundMarker.id))
                  .filter(SoundMarker.chapter_id.in_(chapter_ids), SoundMarker.status == "active",
                          SoundMarker.kind == "scene")
                  .group_by(SoundMarker.chapter_id).all())
    done: dict[str, int] = {}
    for chapter_id, _marker_id in (
        done_tracks(db, AmbientTrack.chapter_id, AmbientTrack.marker_id)
        .join(SoundMarker, SoundMarker.id == AmbientTrack.marker_id)
        .filter(AmbientTrack.chapter_id.in_(chapter_ids), SoundMarker.status == "active",
                SoundMarker.kind == "scene")
        .distinct().all()
    ):
        done[chapter_id] = done.get(chapter_id, 0) + 1
    # Готовый трек, чей файл не прошёл сверку: архив главы из-за него не соберётся
    # (`chapter_has_broken_files`), а перезаливать тут диктору нечего — нужен новый трек.
    broken: dict[str, int] = {}
    for chapter_id, _audio_id in (
        done_tracks(db, AmbientTrack.chapter_id, AudioFile.id)
        .join(SoundMarker, SoundMarker.id == AmbientTrack.marker_id)
        .filter(AmbientTrack.chapter_id.in_(chapter_ids), SoundMarker.status == "active",
                SoundMarker.kind == "scene", AudioFile.verify_state.in_((MISMATCH, MISSING)))
        .distinct().all()
    ):
        broken[chapter_id] = broken.get(chapter_id, 0) + 1
    keys = [f"ambient:{chapter_id}" for chapter_id in chapter_ids]
    last_run = _latest_runs(db, keys)
    last_chapter_run = _latest_runs(db, keys, BackgroundRun.meta_json.like(_CHAPTER_WIDE_META))
    cutoff = utcnow_naive() - timedelta(minutes=STALE_RUN_MINUTES)

    out: dict[str, dict] = {}
    for chapter_id in chapter_ids:
        key = f"ambient:{chapter_id}"
        todo = max(0, int(scenes.get(chapter_id, 0)) - done.get(chapter_id, 0)) if chapter_id in ready else 0
        fields = dict(AMBIENT_EMPTY, ambient_todo=todo, ambient_done=done.get(chapter_id, 0),
                      ambient_broken=broken.get(chapter_id, 0))
        chapter_run = last_chapter_run.get(key)
        if chapter_run is not None:
            chapter_meta = _run_meta(chapter_run)
            result = chapter_meta.get("result") if isinstance(chapter_meta.get("result"), dict) else {}
            fields["ambient_skipped"] = int(result.get("tracks_skipped") or 0)
        run = last_run.get(key)
        state = ""
        if run is not None:
            meta = _run_meta(run)
            result = meta.get("result") if isinstance(meta.get("result"), dict) else {}
            fields["ambient_failed"] = int(meta.get("tracks_failed") or 0)
            last_sign = run.heartbeat_at or run.started_at or run.created_at
            dead = run.status == "running" and last_sign is not None and last_sign < cutoff
            if run.status in ("queued", "running") and not dead:
                state = "running"
            elif result.get("status") in ("quota", "bad_key", "disk_full"):
                state = result["status"]
            elif dead or run.status == "failed" or result.get("status") == "failed" \
                    or fields["ambient_failed"]:
                state = "failed"
        if (not state and chapter_id in ready and fields["ambient_done"]
                and (not todo or todo <= fields["ambient_skipped"])):
            state = "done"
        fields["ambient_state"] = state
        out[chapter_id] = fields
    return out


def find_take(db, audio_id: str) -> AudioFile | None:
    """Дубль по идентификатору. Проба по тому же пути не отдаётся: у неё свой маршрут.

    Зеркало `find_audition` (`app/services/auditions.py`), и разделение то же самое:
    один файл не должен быть достижим двумя именами — иначе право, навешенное на одну
    дверь, обходится через вторую.
    """
    item = db.get(AudioFile, str(audio_id or "").strip())
    if item is None or str(item.kind or "") != TAKE:
        return None
    return item


def _take_row(item: AudioFile) -> dict:
    """Один дубль в описи главы — тем, чем его опознают на слух и на диске.

    `stored_key` здесь не для интерфейса, а для человека за терминалом: опись затевалась
    потому, что дотянуться до файла иначе нечем. `md5` и состояние зеркала — чтобы
    «файл есть» и «файл цел и скопирован» не выглядели одинаково.
    """
    return {
        "id": str(item.id),
        "role": str(item.role or ""),
        "actor_name": str(item.actor_name or ""),
        "original_filename": str(item.original_filename or ""),
        "canonical_filename": str(item.canonical_filename or ""),
        "mime_type": str(item.mime_type or "audio/wav"),
        "size_bytes": int(item.size_bytes or 0),
        "duration_seconds": float(item.duration_seconds or 0.0),
        "uploaded_at": iso_utc(item.uploaded_at) or "",
        "stored_key": str(item.stored_key or ""),
        "location": str(getattr(item, "location", None) or "local"),
        "mirror_state": str(item.mirror_state or ""),
        "mirrored_at": iso_utc(item.mirrored_at) or "",
        "md5": str(item.md5 or ""),
    }


def chapter_recording_status(db, chapter_id: str) -> dict | None:
    """Кто из говорящих ролей главы уже записан. `None` — главы нет."""
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return None
    label = _chapter_label(chapter)
    counts = chapter_role_counts(db, chapter.id)
    actors = {
        str(name or "").strip(): str(actor or "").strip()
        for name, actor in db.query(Character.name, Character.actor_name).filter(Character.book_id == chapter.book_id).all()
    }
    takes = (
        db.query(AudioFile)
        .filter(AudioFile.kind == TAKE, AudioFile.chapter == label)
        .order_by(AudioFile.uploaded_at.asc())
        .all()
    )
    by_role: dict[str, list[AudioFile]] = {}
    for item in takes:
        by_role.setdefault(str(item.role or "").strip(), []).append(item)

    heard = _recognised_by_audio(db, [item.id for item in takes])

    rows = []
    for role, lines in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].lower())):
        found = by_role.get(role, [])
        # Лучший дубль, а не последний: актёр перезаписывает роль, когда первый не удался.
        best = max((heard[item.id] for item in found if item.id in heard),
                   key=lambda entry: entry["coverage"], default=None)
        rows.append({
            "role": role,
            "actor_name": actors.get(role, ""),
            "lines": int(lines),
            "files": len(found),
            # None, а не ноль: ноль означал бы «ничего не произнесено», а мы не считали.
            # Знаменатель у роли свой: диктор отвечает за свои реплики, а не за главу.
            "asr_coverage": best["coverage"] if best else None,
            "matched_lines": best["matched"] if best else 0,
            "missing_lines": best["missing"] if best else 0,
            "duration_seconds": round(sum(float(item.duration_seconds or 0.0) for item in found), 3),
            "size_bytes": sum(int(item.size_bytes or 0) for item in found),
            "takes": [_take_row(item) for item in found],
        })

    # Дубль, чья роль ушла из сценария: её переименовали в карте персонажей или слили
    # с другой. В свою строку он уже не попадёт никогда, а на диске остался — и без
    # этой корзины опись главы молчала бы о нём.
    orphans = [
        item
        for role, found in by_role.items()
        if role not in counts
        for item in found
    ]

    recorded = sum(1 for row in rows if row["files"])
    # Покрытие главы считается от всех её реплик, а не от проверенных файлов: у
    # Рассказчика одного бывает пятая часть текста, и без него глава не может быть готова.
    total_lines = sum(int(row["lines"]) for row in rows)
    matched_lines = sum(int(row["matched_lines"]) for row in rows)
    return {
        "chapter_id": str(chapter.id),
        "total_lines": total_lines,
        "matched_lines": matched_lines,
        "asr_coverage": (matched_lines / total_lines) if (total_lines and any(row["asr_coverage"] is not None for row in rows)) else None,
        "chapter_index": int(chapter.chapter_index or 0),
        "chapter_title": str(chapter.chapter_title or ""),
        "roles": rows,
        "orphan_takes": [_take_row(item) for item in sorted(orphans, key=lambda item: (str(item.role or ""), item.uploaded_at))],
        "total_roles": len(rows),
        "recorded_roles": recorded,
        # пустая глава не «готова»: готовить в ней нечего
        "ready": bool(rows) and recorded == len(rows),
        "duration_seconds": round(sum(row["duration_seconds"] for row in rows), 3),
        "size_bytes": sum(row["size_bytes"] for row in rows),
    }


def _chapter_heading(status: dict) -> str:
    """«Глава 15. От тебя такого не ожидал» — один раз, а не дважды.

    Название главы у этих книг само начинается с номера, и приписанный спереди «Глава N»
    удваивал его.
    """
    title = str(status.get("chapter_title") or "").strip()
    index = int(status.get("chapter_index") or 0)
    if title.lower().startswith("глава"):
        return title
    return f"Глава {index}. {title}" if title else f"Глава {index}"


def _recognised_by_audio(db, audio_ids: list[str]) -> dict[str, dict]:
    """Что распознавание сказало про каждый файл — числами, а не долей.

    Доля здесь бесполезна: складывать доли нельзя, а покрытие главы — это найденные
    реплики, делённые на все её реплики. Именно попытка усреднить доли по файлам и
    давала «сто процентов» на главе, где записана одна роль из двадцати шести.
    """
    if not audio_ids:
        return {}
    out: dict[str, dict] = {}
    for job in db.query(AsrJob).filter(AsrJob.audio_file_id.in_(audio_ids), AsrJob.status == "done").all():
        try:
            payload = json.loads(job.alignment_json or "{}")
        except ValueError:
            payload = {}
        total = int(payload.get("total") or 0)
        missing = len(payload.get("missing") or [])
        out[str(job.audio_file_id)] = {
            "matched": max(0, total - missing),
            "total": total,
            "missing": missing,
            "coverage": (total - missing) / total if total else 0.0,
        }
    return out


def _manifest(book: ScriptBook, status: dict, files: list[AudioFile],
              ambient: list[AudioFile] | None = None) -> str:
    """Опись главы — первое, что откроет тот, кто распакует архив. Эмбиент — своим разделом:
    к записи он не относится и в роли не входит."""
    lines = [
        f"Книга: {str(getattr(book, 'display_title', '') or book.title)}",
        _chapter_heading(status),
        f"Ролей: {status['recorded_roles']} из {status['total_roles']}",
        "",
        "файл\tроль\tактёр\tреплик\tминут",
    ]
    by_role = {row["role"]: row for row in status["roles"]}
    for item in files:
        row = by_role.get(str(item.role or "").strip(), {})
        minutes = float(item.duration_seconds or 0.0) / 60.0
        lines.append(
            f"{item.canonical_filename}\t{item.role}\t{item.actor_name}\t{row.get('lines', '')}\t{minutes:.1f}"
        )
    missing = [row["role"] for row in status["roles"] if not row["files"]]
    if missing:
        lines += ["", "НЕ ЗАПИСАНЫ: " + ", ".join(missing)]
    if ambient:
        lines += ["", "Эмбиент", "файл\tминут"]
        lines += [f"{item.canonical_filename}\t{float(item.duration_seconds or 0.0) / 60.0:.1f}" for item in ambient]
    return "\n".join(lines) + "\n"


def chapter_ambient_files(db, chapter_id: str) -> list[AudioFile]:
    """Файлы готовых (`done`) треков эмбиента активных сцен главы — те, что может назвать
    проект главы. Для архива: `.sesx` в нём зовёт их по имени рядом с дублями."""
    from app.models import AmbientTrack, SoundMarker
    from app.services.ambient_engine import done_tracks

    return (done_tracks(db, AudioFile)
            .join(SoundMarker, SoundMarker.id == AmbientTrack.marker_id)
            .filter(AmbientTrack.chapter_id == chapter_id, SoundMarker.status == "active",
                    SoundMarker.kind == "scene")
            .order_by(AudioFile.canonical_filename.asc())
            .all())


class StorageUnavailable(RuntimeError):
    """Часть файлов лежит на NAS, а он не подтвердил, что жив, — читать их нельзя."""


def build_chapter_archive(db, chapter_id: str, target_path: str) -> dict | None:
    """Сложить дубли главы в архив по указанному пути. `None` — главы нет.

    Без сжатия: WAV им почти не жмётся, а пачка на полгигабайта, сложенная как есть,
    собирается со скоростью диска вместо минут работы процессора.
    """
    status = chapter_recording_status(db, chapter_id)
    if status is None:
        return None
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    book = db.get(ScriptBook, chapter.book_id)
    files = (
        db.query(AudioFile)
        .filter(AudioFile.kind == TAKE, AudioFile.chapter == _chapter_label(chapter))
        .order_by(AudioFile.role.asc(), AudioFile.uploaded_at.asc())
        .all()
    )

    # Эмбиент — в архив рядом с дублями (сессия зовёт его по имени), но в счёт записи
    # не идёт: `files_written`/`files_missing` — по-прежнему только дубли.
    ambient = chapter_ambient_files(db, chapter.id)
    # Старые записи ещё лежат на NAS. Без подтверждённого «жив» к ним не идём вовсе:
    # мёртвое монтирование держит запрос вместе с сессией базы, а архив без части
    # дублей звукорежиссёр принял бы за полный. До ZIP и до отметки «сдана».
    if nas_health.nas_online() is not True and any(
        str(item.location or "local") == "nas" for item in (*files, *ambient)
    ):
        raise StorageUnavailable(chapter.id)
    written, skipped = 0, []
    ambient_written, ambient_skipped = 0, []
    session = build_chapter_session(db, chapter_id, relative=True)
    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr("опись.txt", _manifest(book, status, files, ambient))
        if session:
            # Сессия лежит рядом с файлами и зовёт их по имени: распаковал — и открывай.
            archive.writestr("сессия.sesx", session)
        for item in files:
            source = audio_storage.resolve_path(str(item.stored_key or ""), location=str(item.location or "local"))
            if not os.path.isfile(source):
                skipped.append(str(item.canonical_filename or ""))
                continue
            archive.write(source, arcname=str(item.canonical_filename or os.path.basename(source)))
            written += 1
        for item in ambient:
            source = audio_storage.resolve_path(str(item.stored_key or ""), location=str(item.location or "local"))
            if not os.path.isfile(source):
                ambient_skipped.append(str(item.canonical_filename or ""))
                continue
            archive.write(source, arcname=str(item.canonical_filename or os.path.basename(source)))
            ambient_written += 1
    # Сдана — это полная глава, ушедшая в сведение: все роли записаны и каждый дубль
    # лёг в архив. Архив недописанной главы берут посмотреть, и такое скачивание
    # раньше тоже ставило «сдана» — глава пропадала из ожидающих недописанной.
    # Однажды сданная остаётся сданной: повторное скачивание её не разжалует.
    delivered = bool(status.get("ready")) and not skipped
    if delivered and chapter.delivered_at is None:
        chapter.delivered_at = utcnow_naive()
    code = book_token(derive_book_code(str(book.title or ""))) or "BOOK"
    part = chapter_token(str(chapter.chapter_index or "")) or "Ch00"
    return {
        **status,
        "archive_name": f"{code}_{part}.zip",
        "files_written": written,
        "files_missing": skipped,
        "ambient_written": ambient_written,
        "ambient_missing": ambient_skipped,
        "delivered": chapter.delivered_at is not None,
    }


#: пауза между репликами в черновой сборке — столько, чтобы фразы не слипались
GAP_SECONDS = 0.35
#: сколько знаков произносят за секунду; по этому оценивается дыра под пропущенную реплику
CHARS_PER_SECOND = 14.0
#: дорожки, которые монтажёр заводит руками перед каждой главой. Пустые, но заведённые:
#: две под музыку, две под звуки и шумы.
EXTRA_TRACKS = ("Саундтрек 1", "Саундтрек 2", "Интершум 1", "Интершум 2")


def build_chapter_session(db, chapter_id: str, *, sample_rate: int = 44100, relative: bool = False) -> str | None:
    """Сессия Audition для главы: треки по ролям, клипы по репликам. `None` — главы нет.

    Тонкая обёртка над `build_chapter_session_with_stats` — большинству вызывающих число
    пропущенных маркеров не нужно, а сигнатура этой функции старая и на ней держится
    архивация и раздача главы.
    """
    xml, _skipped = build_chapter_session_with_stats(db, chapter_id, sample_rate=sample_rate, relative=relative)
    return xml


def build_chapter_session_with_stats(db, chapter_id: str, *, sample_rate: int = 44100,
                                     relative: bool = False) -> tuple[str | None, int]:
    """То же самое, что `build_chapter_session`, и вдобавок число маркеров без места.

    `None, 0` — главы нет. Число пропущенных — так архивация (см. `archive_chapter_session`)
    узнаёт его без второй сборки сессии: обе стоят одного прохода по репликам и разметке.

    Клипы стоят в порядке сценария, а не в порядке файла своего актёра. Каждый диктор
    писал отдельно и начинал с нуля, поэтому позиции внутри его записи ни с чем не
    согласованы: разложенные как есть, два трека звучали бы одновременно. Порядок знает
    сценарий — и по нему глава открывается собранным черновиком, где остаётся выверить
    паузы и свести.

    Вырезается при этом по-прежнему то самое место, где реплика произнесена: новое у
    клипа только место на таймлайне.

    Пропущенная реплика оставляет за собой паузу по длине своего текста. Дыра на своём
    месте говорит монтажёру и чего не хватает, и куда оно встанет.

    Без распознавания класть по репликам нечего: трек несёт один целый дубль. Это уже
    полдела — файлы разложены по трекам с именами ролей, остаётся резать, а не искать.
    Маркеры звукорежиссёра по той же причине без распознавания не встают вовсе: без
    начала абзацев `starts` пуст, и `session_markers` честно отдаёт всё как пропущенное.
    """
    layout = _script_layout(db, chapter_id, relative=relative)
    if layout is None:
        return None, 0
    status, book, by_role, placed, starts, end = layout

    clips_by_role: dict[str, list[SessionClip]] = {row["role"]: [] for row in status["roles"]}
    for role, clip in placed:
        clips_by_role.setdefault(role, []).append(clip)

    # Роли, по которым распознавания нет, кладём целым дублем: лучше так, чем никак.
    for row in status["roles"]:
        if clips_by_role.get(row["role"]):
            continue
        for item in by_role.get(row["role"], []):
            clips_by_role.setdefault(row["role"], []).append(SessionClip(
                name=str(item.canonical_filename or row["role"]),
                path=audio_storage.resolve_path(str(item.stored_key or ""), location=str(item.location or "local")),
                start=0.0,
                source_in=0.0,
                source_out=float(item.duration_seconds or 0.0),
                relative=str(item.canonical_filename or "") if relative else "",
            ))

    tracks = [SessionTrack(name=row["role"], clips=clips_by_role.get(row["role"], [])) for row in status["roles"]]
    try:
        tracks += ambient_session_tracks(db, chapter_id, starts, end, relative=relative)
    except Exception:
        # Эмбиент — подложка поверх записанной главы: его сбой не должен срывать
        # архивацию дублей, как и сбой маркеров ниже.
        logger.exception("эмбиент не лёг в сессию главы %s — сессия соберётся без него", chapter_id)
    tracks += [SessionTrack(name=name) for name in EXTRA_TRACKS]
    # Витрина «Автор - "Название"», а не голый `title`: имя сессии монтажёр читает,
    # а код книги для имён файлов берётся из `title` отдельно (ниже, `derive_book_code`).
    book_name = str(getattr(book, "display_title", "") or getattr(book, "title", "") or "") if book else ""
    title = f"{book_name} · {_chapter_heading(status)}".strip(" ·")
    try:
        markers, skipped = session_markers(db, chapter_id, starts, end)
    except Exception:
        # Один битый маркер (например, испорченный segment_id) не должен срывать
        # архивацию всей главы — дубли записаны, монтажёру нужна хотя бы раскладка
        # без разметки звукорежиссёра, а не отказ вовсе.
        logger.exception("session_markers сорвались на главе %s — сессия соберётся без маркеров", chapter_id)
        markers, skipped = [], 0
    xml = build_session_xml(title=title, sample_rate=sample_rate, tracks=tracks, markers=markers)
    return xml, skipped


def _script_layout(db, chapter_id: str, *, relative: bool = False):
    """Раскладка главы по сценарию — общая для `.sesx` и `scene_spans`.

    `None` — главы нет. Иначе `(status, book, by_role, placed, starts, end)`: статус записи,
    книга, файлы дублей по ролям, разложенные клипы, начало каждого записанного абзаца и
    конец разложенного. Одна функция на обоих — чтобы длина сцены у эмбиента и у
    маркера-отрезка в проекте не разошлись.
    """
    status = chapter_recording_status(db, chapter_id)
    if status is None:
        return None
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    book = db.get(ScriptBook, chapter.book_id)
    # Не "takes" — ниже, в `_place_by_script`/`_takes_of`, это слово уже значит
    # подходы диктора внутри одной реплики; здесь же это файлы дублей на диске.
    audio_files = (
        db.query(AudioFile)
        .filter(AudioFile.kind == TAKE, AudioFile.chapter == _chapter_label(chapter))
        .order_by(AudioFile.uploaded_at.asc())
        .all()
    )
    by_role: dict[str, list[AudioFile]] = {}
    for item in audio_files:
        by_role.setdefault(str(item.role or "").strip(), []).append(item)
    heard = _lines_by_audio(db, [item.id for item in audio_files])

    replicas = chapter_replicas(db, chapter_id)
    starts: dict[str, float] = {}
    placed = _place_by_script(replicas, by_role, heard, relative=relative,
                              files_by_id={str(item.id): item for item in audio_files}, starts=starts)
    # Конец сцены до конца главы — это конец РАЗЛОЖЕННЫХ по сценарию клипов, а не целых
    # дублей запасного пути: у тех нет согласованного места на общем таймлайне (см.
    # докстринг `_place_by_script`), и мерить сцену их концом было бы враньём монтажёру.
    end = max((clip.start + clip.length for _role, clip in placed), default=0.0)
    return status, book, by_role, placed, starts, end


def active_scene_rows(db, chapter_id: str) -> tuple[list[dict], dict]:
    """Активные сцены главы по порядку и их места — как `active_chapter_markers`, но только
    сцены и без битых: маркер с испорченным `segment_id` пропускается с предупреждением, а не
    срывает главу (та же защита, что у сборки `.sesx`). Только чтение."""
    from app.models import SoundMarker, SoundPlace
    from app.services.sound_store import _view

    rows = []
    for row in (db.query(SoundMarker).filter(SoundMarker.chapter_id == chapter_id,
                                             SoundMarker.status == "active", SoundMarker.kind == "scene")):
        try:
            ordinal = _marker_ordinal(row.segment_id)
        except (ValueError, IndexError):
            logger.warning("сцена %s с битым segment_id %r пропущена", row.id, row.segment_id)
            continue
        rows.append((ordinal, row))
    rows.sort(key=lambda pair: (pair[0], pair[1].created_at, pair[1].id))
    place_ids = {row.place_id for _ordinal, row in rows if row.place_id}
    places = ({p.id: p for p in db.query(SoundPlace).filter(SoundPlace.id.in_(place_ids))}
              if place_ids else {})
    return [_view(row) for _ordinal, row in rows], places


def scene_spans(db, chapter_id: str) -> dict[str, tuple[float, float]]:
    """Сцены главы на таймлайне проекта: `marker_id` → (начало, длина), в секундах.

    Те же правила, что у маркера-отрезка сцены в `.sesx` (`session_markers`): начало —
    первый записанный абзац не раньше абзаца сцены, длина — до следующей сцены со строго
    бОльшим началом или до конца главы. Сцены без места на таймлайне (дальше ничего не
    записано, или отрезок проглотила соседка) в словарь не входят. Только чтение.
    """
    layout = _script_layout(db, chapter_id)
    if layout is None:
        return {}
    _status, _book, _by_role, _placed, starts, end = layout
    return _spans_on_timeline(db, chapter_id, starts, end)


def _spans_on_timeline(db, chapter_id: str, starts: dict[str, float], end: float) -> dict[str, tuple[float, float]]:
    """`scene_spans` по уже собранной раскладке — сборке `.sesx` незачем раскладывать главу дважды."""
    rows, _places = active_scene_rows(db, chapter_id)
    placed, _skipped = _placed_markers(rows, starts)
    scenes = [(row, start) for row, start, _shifted in placed]
    durations = _scene_durations([start for _row, start in scenes], end)
    return {row["id"]: (start, duration) for (row, start), duration in zip(scenes, durations)
            if duration is not None}


#: дорожки эмбиента: сцены главы ложатся на них по очереди, чтобы соседние подложки
#: не резали друг друга и монтажёру было где сделать кроссфейд
AMBIENT_TRACKS = ("Ambient 1", "Ambient 2")
#: вид строки `AudioFile` эмбиента — тот же, что пишет `ambient_engine.KIND`
AMBIENT_KIND = "ambient"


def ambient_session_tracks(db, chapter_id: str, starts: dict[str, float], end: float, *,
                           relative: bool = False) -> list[SessionTrack]:
    """Дорожки «Ambient 1/2» проекта главы: готовые (`done`) треки сцен, нашедших место на
    таймлайне, по порядку начала сцены — поочерёдно, каждый целиком от начала своей сцены.

    Пусто — ни одного такого трека: тогда дорожек нет вовсе, и проект главы без эмбиента
    остаётся байт в байт прежним. Трек снятой сцены (или сцены без места) лежит в папке, но
    в проект не кладётся; перенесённая сцена уносит свой трек на новое место.
    """
    from app.models import AmbientTrack
    from app.services.ambient_engine import done_tracks

    rows = (done_tracks(db, AmbientTrack, AudioFile)
            .filter(AmbientTrack.chapter_id == chapter_id)
            .order_by(AmbientTrack.created_at.asc(), AmbientTrack.id.asc())
            .all())
    if not rows:
        return []
    # Готовый трек у сцены один (перегенерация переводит прежний в `replaced`); на всякий
    # случай берётся самый свежий.
    file_of = {track.marker_id: item for track, item in rows}
    spans = _spans_on_timeline(db, chapter_id, starts, end)
    placed = sorted(((spans[marker_id][0], index, file_of[marker_id])
                     for index, marker_id in enumerate(spans) if marker_id in file_of),
                    key=lambda entry: (entry[0], entry[1]))
    if not placed:
        return []
    tracks = [SessionTrack(name=name) for name in AMBIENT_TRACKS]
    for number, (start, _index, item) in enumerate(placed):
        tracks[number % len(tracks)].clips.append(SessionClip(
            name=str(item.canonical_filename or ""),
            path=audio_storage.resolve_path(str(item.stored_key or ""), location=str(item.location or "local")),
            start=float(start),
            source_in=0.0,
            source_out=float(item.duration_seconds or 0.0),
            relative=str(item.canonical_filename or "") if relative else "",
        ))
    return tracks


def _marker_guid(marker_id: str) -> str:
    # Детерминированный, а не случайный: иначе каждая пересборка меняла бы GUID, а
    # `session_fingerprint` (не видящий его как «компонентный», но видящий как часть
    # XML) считал бы раскладку изменившейся на каждый круг фоновой архивации.
    return f"xmp:id:{uuid5(NAMESPACE_URL, f'noname-sound:{marker_id}')}"


_SHIFTED_NOTE = "абзац не записан, маркер сдвинут"


def _resolve_marker_start(timeline: list[tuple[int, float]], ordinal: int) -> tuple[float, bool] | None:
    """Начало маркера на абзаце `ordinal`: свой, если абзац записан, иначе — следующего
    записанного абзаца главы (флаг «сдвинут»). Дальше записанного нет — `None`."""
    for line_ordinal, start in timeline:
        if line_ordinal >= ordinal:
            return start, line_ordinal != ordinal
    return None


def _scene_comment(payload: dict, place) -> str:
    bits = []
    ambience = str(payload.get("ambience") or "").strip()
    if ambience:
        bits.append(f"фон: {ambience}")
    mood = str(payload.get("mood") or "").strip()
    if mood:
        bits.append(f"настроение: {mood}")
    queries = list((place.ambience_queries or []) if place is not None else []) + list(payload.get("music_queries") or [])
    bits += [f"🔎 {q}" for q in queries if str(q or "").strip()]
    return " · ".join(bits)


def _sound_comment(quote: str, payload: dict) -> str:
    bits = []
    quote = str(quote or "").strip()
    if quote:
        bits.append(f"«{quote}»")
    bits += [f"🔎 {q}" for q in (payload.get("queries") or []) if str(q or "").strip()]
    return " · ".join(bits)


def _scene_name(number: int, place_name: str, payload: dict) -> str:
    parts = [f"СЦЕНА {number}", place_name, str(payload.get("time_of_day") or "").strip(),
            str(payload.get("weather") or "").strip()]
    return " · ".join(part for part in parts if part)


def _scene_durations(scene_starts: list[float], end: float) -> list[float | None]:
    """Длительность каждой размещённой сцены: до ближайшего СТРОГО большего начала
    среди более поздних сцен, а нет такой — до конца главы. `None` — сцену целиком
    проглотила соседняя (положительного отрезка не вышло: две сцены легли на один и тот
    же непроставленный абзац, или порядок начал разъехался) — такую не ставим точкой,
    а пропускаем и считаем пропущенной.
    """
    out: list[float | None] = []
    for index, start in enumerate(scene_starts):
        later = [candidate for candidate in scene_starts[index + 1:] if candidate > start]
        limit = min(later) if later else end
        duration = limit - start
        out.append(duration if duration > 0 else None)
    return out


def _placed_markers(rows: list[dict], starts: dict[str, float]) -> tuple[list[tuple[dict, float, bool]], int]:
    """Маркеры, нашедшие место на таймлайне: `(маркер, начало, сдвинут)`, и число без места."""
    timeline = sorted((_marker_ordinal(segment_id), start) for segment_id, start in starts.items())
    placed: list[tuple[dict, float, bool]] = []
    skipped = 0
    for row in rows:
        found = _resolve_marker_start(timeline, _marker_ordinal(row["segment_id"]))
        if found is None:
            skipped += 1
            continue
        start, shifted = found
        placed.append((row, start, shifted))
    return placed, skipped


def session_markers(db, chapter_id: str, starts: dict[str, float], end: float) -> tuple[list[SessionMarker], int]:
    """Активные маркеры главы, разложенные по таймлайну проекта, и число пропущенных.

    Номер «СЦЕНА N» — по порядку сцен в главе по абзацу, тому же, что видит редактор
    разметки: считается по ВСЕМ активным сценам, а не только по тем, что нашли место на
    таймлайне, — иначе номер той же сцены расходился бы между вкладкой разметки и
    сессией Audition в главе, где начало ещё не дописано. Длительность сцены — до
    следующей сцены со строго бОльшим началом (см. `_scene_durations`); если такой
    нет и до конца главы тоже нечего тянуть, сцену пропускаем — точкой она не
    становится никогда, только отрезком или ничем.
    """
    rows, places = active_chapter_markers(db, chapter_id)
    scene_number = {row["id"]: i + 1 for i, row in enumerate(row for row in rows if row["kind"] == "scene")}
    placed, skipped = _placed_markers(rows, starts)

    scene_starts = [start for row, start, _shifted in placed if row["kind"] == "scene"]
    scene_durations = _scene_durations(scene_starts, end)

    markers: list[SessionMarker] = []
    scene_seen = 0
    for row, start, shifted in placed:
        payload = row["payload"]
        if row["kind"] == "scene":
            duration = scene_durations[scene_seen]
            scene_seen += 1
            if duration is None:
                # Сцену целиком проглотила соседняя — не размечать точкой, а пропустить.
                skipped += 1
                continue
            place = places.get(row["place_id"]) if row.get("place_id") else None
            place_name = place.name if place is not None else "без места"
            name = _scene_name(scene_number[row["id"]], place_name, payload)
            comment = _scene_comment(payload, place)
        elif row["kind"] == "sound":
            name = f"● {payload.get('description', '')}"
            comment = _sound_comment(row["quote"], payload)
            duration = 0.0
        else:  # transition
            name = f"◆ {payload.get('what', '')}"
            comment = ""
            duration = 0.0
        if shifted:
            comment = f"{comment} · {_SHIFTED_NOTE}" if comment else _SHIFTED_NOTE
        markers.append(SessionMarker(name=name, start=start, duration=duration, comment=comment,
                                     guid=_marker_guid(row["id"])))

    markers.sort(key=lambda m: m.start)
    return markers, skipped


def _takes_of(line: dict) -> list[dict]:
    """Подходы реплики из выровненной строки.

    Записи, сделанные до появления подходов, хранят одно вхождение в `start`/`end` и
    ключа `takes` не имеют. Перераспознавать их ради сессии — минута машинного времени
    на файл, поэтому читаем и старый вид.
    """
    takes = line.get("takes")
    if isinstance(takes, list) and takes:
        filtered = [
            {"start": float(take["start"]), "end": float(take["end"])}
            for take in takes
            if take.get("start") is not None and take.get("end") is not None
        ]
        if filtered:
            return filtered
        # `takes` был непуст, но каждый подход в нём — брак (без start/end):
        # тот же откат, что и при отсутствии ключа вовсе, а не молчаливая дыра.
    if line.get("start") is not None and line.get("end") is not None:
        return [{"start": float(line["start"]), "end": float(line["end"])}]
    return []


def _clip_name(text: str, number: int, total: int) -> str:
    """Имя клипа. Единственный подход не нумеруется: «дубль 1/1» — шум."""
    head = str(text or "")[:80]
    return head if total <= 1 else f"{head} · дубль {number}/{total}"


def _place_by_script(replicas, by_role, heard, *, relative: bool, files_by_id=None,
                     starts: dict[str, float] | None = None) -> list[tuple[str, SessionClip]]:
    """Разложить реплики по таймлайну одну за другой, как они идут в главе.

    `files_by_id` — все файлы главы: строка, найденная у соседней роли того же актёра
    (`asr_borrow`), несёт `source_audio_file_id`, и клип режется из того файла, а ложится
    на дорожку роли реплики.

    `starts` — если передан, дописывается началом САМОГО РАННЕГО размещённого клипа
    каждого абзаца (`replica["segment_id"]` — id того же абзаца, что и в
    `sound_markers.segment_id`, формат совпадает не случайно: `V2Segment.id` собран как
    `f"{chapter_id}:{ordinal:05d}"`, той же функцией, что и `SoundMarker.segment_id`).
    Абзац может отдать несколько реплик подряд (несколько ролей в одном предложении, или
    роль читает две реплики) — и обработанная раньше не всегда стоит раньше на
    таймлайне: если роль уже занята подходами более ранней реплики, её офсет может
    обогнать общие часы (см. `track_free_at` ниже), а следующая реплика того же абзаца,
    но другой, ещё свободной роли, встанет по часам — то есть раньше. Поэтому запись —
    минимум увиденных офсетов, а не первый из них.
    """
    files_by_id = files_by_id or {}
    placed: list[tuple[str, SessionClip]] = []
    clock = 0.0
    # До какого момента занята дорожка каждой роли её же подходами (плюс зазор).
    # Общие часы одни на всех и двигаются только на САМЫЙ ДЛИННЫЙ подход реплики —
    # иначе треки начали бы расходиться друг с другом. Но подходы одной реплики
    # ложатся на СВОЮ дорожку встык и занимают её на СУММУ своих длительностей.
    # Пока следующая реплика на другой роли — это не важно: общих часов хватает.
    # Когда роль читает две реплики подряд (рассказчик; реплика персонажа,
    # разбитая на части), вторая должна встать не раньше, чем освободилась
    # дорожка от подходов первой, а не только не раньше общих часов — иначе
    # хвост подходов первой реплики наедет на клип второй.
    track_free_at: dict[str, float] = {}
    for replica in replicas:
        role = str(replica.get("role") or "")
        candidates = []
        for position, item in enumerate(by_role.get(role, [])):
            lines = heard.get(item.id) or []
            index = int(replica.get("role_index") or 0)
            if index < len(lines) and lines[index].get("matched"):
                takes = _takes_of(lines[index])
                source_id = str(lines[index].get("source_audio_file_id") or "")
                source = files_by_id.get(source_id) if source_id else item
                if takes and source is not None:
                    candidates.append((getattr(source, "uploaded_at", None), position, source, takes))
        # Реплика бывает в нескольких файлах роли: общий файл и фикс, старый и присланный
        # заново. Звучит последний присланный — его и прислали, чтобы исправить (решение
        # владельца 2026-09-15). Старые файлы остаются в описи главы. При равном времени
        # (или его отсутствии — старые записи в тестах без `uploaded_at`) побеждает тот,
        # что позже встретился в `by_role` — он приходит уже отсортированным по загрузке.
        found = None
        if candidates:
            _at, _pos, source, takes = max(candidates, key=lambda c: (c[0] or datetime.min, c[1]))
            found = (source, takes)
        if found is None:
            # Пропущенная реплика — пауза по длине её текста, чтобы дыра была видна
            clock += max(1.0, len(str(replica.get("text") or "")) / CHARS_PER_SECOND) + GAP_SECONDS
            continue
        item, takes = found
        path = audio_storage.resolve_path(str(item.stored_key or ""), location=str(item.location or "local"))
        # Подходы ложатся встык от начала реплики: монтажёр слушает их подряд и удаляет
        # лишние. Система не решает, какой лучше, — на дорожке лежит «дубль 1», а не
        # «наш выбор», и это видно по имени клипа.
        #
        # Встают они по общим часам ИЛИ по занятости своей дорожки — что позже.
        # Общих часов достаточно, пока соседняя реплика чужая: её слот начинается
        # там, где предыдущая роль его оставила. Но если эту реплику читает та же
        # роль, что и предыдущую, общие часы могли продвинуться только на самый
        # длинный из ЕЁ ЖЕ подходов, а дорожка занята на их СУММУ — и без своей
        # занятости новый клип лёг бы поверх хвоста предыдущей реплики.
        offset = max(clock, track_free_at.get(role, 0.0))
        segment_id = str(replica.get("segment_id") or "")
        if starts is not None and segment_id:
            starts[segment_id] = min(starts.get(segment_id, offset), offset)
        for number, take in enumerate(takes, start=1):
            length = max(0.0, take["end"] - take["start"])
            placed.append((role, SessionClip(
                name=_clip_name(str(replica.get("text") or ""), number, len(takes)),
                path=path,
                start=offset,
                source_in=take["start"],
                source_out=take["end"],
                relative=str(item.canonical_filename or "") if relative else "",
            )))
            offset += length
        track_free_at[role] = offset + GAP_SECONDS
        # Часы двигаются на САМЫЙ ДЛИННЫЙ подход, а не на сумму и не на первый. По сумме
        # между репликами зияла бы тишина во весь хвост неиспользованных подходов. По
        # первому — выбор более длинного подхода полез бы на следующую реплику, а первый
        # бывает брошенным: диктор начинает, ошибается на середине и читает заново.
        # Промах в большую сторону оставляет дырку, в меньшую — нахлёст; уборка дешевле
        # распутывания.
        clock += max(max(0.0, take["end"] - take["start"]) for take in takes) + GAP_SECONDS
    return placed


def _lines_by_audio(db, audio_ids: list[str]) -> dict[str, list[dict]]:
    """Выровненные реплики по каждому файлу — то, из чего режутся клипы."""
    if not audio_ids:
        return {}
    out: dict[str, list[dict]] = {}
    for job in db.query(AsrJob).filter(AsrJob.audio_file_id.in_(audio_ids), AsrJob.status == "done").all():
        try:
            lines = json.loads(job.alignment_json or "{}").get("lines") or []
        except ValueError:
            continue
        if lines:
            out[str(job.audio_file_id)] = lines
    return out


def mark_session_outdated(db, chapter_id: str) -> bool:
    """Архивная сессия главы перестала соответствовать сверке. `False` — архива нет."""
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None or chapter.session_archived_at is None:
        return False
    chapter.session_outdated_at = utcnow_naive()
    db.add(chapter)
    return True


_COMPONENT_GUID_RE = re.compile(r' componentGuid="[^"]*"')


def session_fingerprint(xml: str) -> str:
    """Отпечаток сессии без GUID компонентов.

    `componentGuid` в `audition_session.build_session_xml` — настоящий случайный
    UUID на каждый вызов (и должен им оставаться: Audition не терпит повторов
    внутри файла, а два файла — архивный и его же пересборка — иногда лежат в
    одной папке и могут быть открыты монтажёром одновременно). Из-за этого сырой
    XML двух сборок одной и той же раскладки никогда не совпадает побайтово, хотя
    реплики, файлы и тайминги в нём те же самые. Отпечаток берётся по XML, из
    которого GUID вычищены, — тогда «изменилось» решает раскладка, а не удача
    генератора случайных чисел.
    """
    return hashlib.sha256(_COMPONENT_GUID_RE.sub("", xml).encode("utf-8")).hexdigest()


def _backup_key(key: str, archived_at) -> str:
    """Имя для прежней сессии: «… до ГГГГ-ММ-ДД ЧЧ-ММ.sesx», занятое — с номером.

    Время в имени — уфимское (`SERVER_TIMEZONE`), а не UTC базы: имя читает монтажёр, и
    «до 10-42» при правке в 15:42 по его часам отправило бы искать не ту версию.
    """
    moment = (archived_at or utcnow_naive()).replace(tzinfo=timezone.utc).astimezone(SERVER_TIMEZONE)
    stamp = moment.strftime("%Y-%m-%d %H-%M")
    base = key[: -len(".sesx")] if key.endswith(".sesx") else key
    candidate = f"{base} до {stamp}.sesx"
    number = 2
    while audio_storage.file_exists(candidate, location="nas"):
        candidate = f"{base} до {stamp} ({number}).sesx"
        number += 1
    return candidate


def archive_chapter_session(db, chapter_id: str, *, book_status: dict | None = None) -> dict | None:
    """Положить файл проекта внутрь папки главы на NAS. `None` — главы нет.

    Отдельного «переноса главы» нет: дубли к этому моменту давно зеркалированы,
    и событие «глава дописана» добавляет только `.sesx`. Условия — пять сразу,
    потому что каждое ловит своё: роли отвечают за полноту записи, распознавание
    за то, что прочитано всё, сверка — за то, что оригинал на сервере цел, зеркало —
    за то, что копия на NAS не разошлась по сумме, а перекрытие корней — за то, что
    это вообще две разные копии, а не одна под двумя именами.
    """
    from app.services.audio_integrity import chapter_files, chapter_has_broken_files, chapter_is_ready
    from app.services.audio_mirror import chapter_folder, roots_overlap

    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return None
    if not chapter_is_ready(db, chapter.id):
        return {"written": False, "reason": "not_ready", "replaced": ""}
    # Статус можно передать снаружи: он считается по книге ЦЕЛИКОМ — полный обход
    # сегментов, атрибуций и дублей, — а фоновому кругу нужен один и тот же ответ
    # для всех глав одной книги. Без этого книга, у которой несколько глав ждут
    # распознавания, пересчитывалась бы по разу на каждую, и так каждые пять минут
    # до конца записи.
    status = book_status if book_status is not None else book_recording_status(db, chapter.book_id)
    row = next((item for item in status["chapters"] if item["chapter_id"] == chapter.id), None)
    if row is None or not row["asr_files"] or row["missing_lines"]:
        return {"written": False, "reason": "asr_gaps", "replaced": ""}
    if chapter_has_broken_files(db, chapter.id):
        return {"written": False, "reason": "integrity_failed", "replaced": ""}
    # `chapter_has_broken_files` смотрит только `verify_state` — целостность
    # оригинала на сервере. Копия на NAS проверяется отдельным полем: у неё могла
    # разойтись сумма при зеркалировании, и `audio_mirror.mirror_once` тогда
    # исключает файл из копирования навсегда (см. его же комментарий про
    # mirror_state == mismatch) — но `.sesx` всё равно ссылается на файлы по
    # именам, и без этой проверки лёг бы на NAS рядом с битой копией.
    # Треки эмбиента проект тоже зовёт по имени — битая копия любого из них так же
    # не пускает `.sesx` на NAS. Ещё не доехавший трек не держит: приедет сам.
    if any(
        str(item.mirror_state or "") == MIRROR_MISMATCH
        for item in [*chapter_files(db, chapter.id), *chapter_ambient_files(db, chapter.id)]
    ):
        return {"written": False, "reason": "mirror_broken", "replaced": ""}
    # Пустая настройка NAS — законное состояние (см. `audio_storage.nas_root()` и
    # `audio_mirror.mirror_once`): без зеркала класть файл проекта некуда, а
    # `resolve_path(..., location="nas")` на пустом корне бросил бы RuntimeError.
    # Отсутствие NAS — не повод падать: приём и без зеркала работает как обычно.
    if not audio_storage.nas_root():
        return {"written": False, "reason": "no_nas", "replaced": ""}
    # Настроенный, но молчащий NAS — другое дело: `hard`-монтирование на мёртвом
    # зеркале не бросает исключение, а виснет навсегда (ровно то, ради чего заведён
    # `nas_health`). "no_nas" здесь не подходит — то состояние никогда само не
    # пройдёт, а это может ожить к следующему кругу `mirror_loop`.
    if nas_health.nas_online() is not True:
        return {"written": False, "reason": "nas_offline", "replaced": ""}
    # Тот же довод, что и в `audio_mirror.mirror_once`: перекрытые корни значат, что
    # обе копии легли бы на один диск. Без этой проверки `.sesx` ушёл бы на локальный
    # диск (потому что `resolve_path` резолвит `location="nas"` в тот же корень), а
    # отметка `session_archived_at` сказала бы «есть на NAS» про файл, которого там нет.
    if roots_overlap():
        return {"written": False, "reason": "roots_overlap", "replaced": ""}

    xml, markers_skipped = build_chapter_session_with_stats(db, chapter.id, relative=True)
    if not xml:
        return {"written": False, "reason": "empty_session", "replaced": ""}
    data = xml.encode("utf-8")
    digest = session_fingerprint(xml)
    # book_code у книги НЕТ — это поле есть только у AudioFile. Код собирается
    # из заголовка теми же инициалами, что пишет диктор в имени файла, и той же
    # функцией, которой пользуется поиск главы по дублю (`asr_run.find_chapter_for_take`).
    book = db.get(ScriptBook, chapter.book_id)
    code = book_token(derive_book_code(str(getattr(book, "title", "") or ""))) or "BOOK"
    folder = chapter_folder(_chapter_label(chapter))
    key = f"{code}/{folder}/{code}_Ch{int(chapter.chapter_index or 0):02d}.sesx"
    # Правка разметки не всегда меняет раскладку сессии: тогда писать нечего, а пометка
    # «устарела» снимается — иначе фон пересобирал бы главу на каждом круге.
    on_nas = audio_storage.file_exists(key, location="nas")
    # Глава, архивированная до отпечатков (миграция 0029), отпечатка не имеет: без сверки
    # с самим файлом на NAS её пересобирали бы при первой же пометке, даже когда
    # раскладка та же. Отпечаток берётся по файлу и запоминается.
    if chapter.session_archived_at is not None and not str(chapter.session_sha256 or "") and on_nas:
        try:
            existing = audio_storage.read_file(key, location="nas").decode("utf-8-sig")
        except (OSError, UnicodeDecodeError):
            existing = None
        if existing is not None and session_fingerprint(existing) == digest:
            chapter.session_sha256 = digest
    if chapter.session_archived_at is not None and chapter.session_sha256 == digest and on_nas:
        chapter.session_outdated_at = None
        chapter.session_markers_skipped = markers_skipped
        db.add(chapter)
        return {"written": False, "reason": "unchanged", "replaced": ""}
    # Прежний файл не затирается никогда: монтажёр мог работать прямо в нём (решение
    # владельца 2026-09-15). Лишняя копия дешевле затёртого монтажа.
    replaced = ""
    if on_nas:
        replaced = _backup_key(key, chapter.session_archived_at)
        audio_storage.move_file(key, replaced, location="nas")
    audio_storage.write_file(key, data, location="nas")
    chapter.session_archived_at = utcnow_naive()
    chapter.session_sha256 = digest
    chapter.session_outdated_at = None
    chapter.session_markers_skipped = markers_skipped
    db.add(chapter)
    return {"written": True, "reason": "", "replaced": replaced}


def archive_pending_chapter_sessions(db) -> dict:
    """Пройти фоновым кругом по главам с дублями и положить файл проекта тем, кто
    его ещё не получил. Единственный законный вызывающий — `app.workers.mirror.mirror_loop`:
    NFS не может быть в пути приёма (см. `archive_chapter_session`), а этот цикл — то
    самое место, которое уже спрашивает сторожа NAS и уже пишет на зеркало вне запроса.

    Фильтр в самом запросе — не только оптимизация: он же и есть правило «класть
    файл только тем, у кого его нет или он устарел». Главу без пометки
    `session_outdated_at` фон больше не трогает; правка разметки или новый дубль
    ставят эту пометку сами (`mark_session_outdated`), и вот тогда круг
    пересобирает `.sesx` заново — прежний файл при этом не стирается, а остаётся
    рядом под именем «... до ГГГГ-ММ-ДД ЧЧ-ММ.sesx» (см. `_backup_key`).

    Метки глав с дублями — фильтр до похода по чтению статуса: без него шестьдесят
    опубликованных глав книги значили бы шестьдесят пересчётов готовности каждый
    круг, хотя дубли есть у трёх.
    """
    labels = {
        str(label or "").strip()
        for (label,) in db.query(AudioFile.chapter).filter(AudioFile.kind == TAKE).distinct().all()
    }
    labels.discard("")
    if not labels:
        return {"archived": 0, "skipped": 0, "rebuilt": 0}

    from sqlalchemy import or_

    chapters = db.query(ScriptChapter).filter(or_(
        ScriptChapter.session_archived_at.is_(None), ScriptChapter.session_outdated_at.isnot(None),
    )).all()
    archived = skipped = rebuilt = 0
    # Статус книги считается один раз на круг, а не один раз на главу: он обходит
    # книгу целиком, и на книге, где распознавания ждут сразу несколько глав, разница
    # ровно в число этих глав — каждые несколько минут, всё время записи.
    status_of_book: dict[str, dict] = {}
    for chapter in chapters:
        if _chapter_label(chapter) not in labels:
            continue
        if chapter.book_id not in status_of_book:
            status_of_book[chapter.book_id] = book_recording_status(db, chapter.book_id)
        result = archive_chapter_session(db, chapter.id, book_status=status_of_book[chapter.book_id])
        if result and result["written"]:
            archived += 1
            if result["replaced"]:
                rebuilt += 1
                _tell_owner_rebuilt(db, chapter, result["replaced"])
            # Коммит на каждую главу, а не один на весь круг: так же, как в
            # `audio_mirror.mirror_once` — сбой на главе N не должен откатывать
            # отметки уже записанных 1..N-1 к следующему кругу.
            db.commit()
        elif result and result["reason"] == "unchanged":
            db.commit()
            skipped += 1
        else:
            skipped += 1
    return {"archived": archived, "skipped": skipped, "rebuilt": rebuilt}


def _tell_owner_rebuilt(db, chapter, replaced_key: str) -> None:
    """Владельцу — одна строка: глава пересобрана, прежняя цела. Сбой письма архив не отменяет."""
    name = replaced_key.rsplit("/", 1)[-1]
    try:
        send_telegram_message(db, f"🎬 Глава {int(chapter.chapter_index or 0)}: сессия пересобрана. "
                                  f"Прежняя сохранена как «{name}».")
    except Exception:
        logger.exception("Не удалось сообщить о пересборке главы %s", chapter.id)
