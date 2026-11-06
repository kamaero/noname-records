"""Находки рассказчика переходят из времени исходника в нарезанный проект.

Таймкод STT нельзя писать в сессию напрямую: каждый актёр записывался с нуля,
а проект разложен по сценарию. Без подходящего клипа безопаснее показать пропуск
метки, чем отправить монтажёра исправлять другое место.
"""
from __future__ import annotations

import json
import logging
import math
from uuid import NAMESPACE_URL, uuid5

from app.services.author_profile import normalize_name
from app.services.audition_session import SessionMarker
from app.services.shared_runtime import is_narrator_name

logger = logging.getLogger(__name__)


def _bounds(anchor):
    if not isinstance(anchor, dict) or anchor.get("mapped") is False:
        return None
    try:
        start, end = float(anchor["start"]), float(anchor["end"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
        return None
    return start, end


def _address_matches(value: dict, replica: dict) -> bool:
    if "segment_id" in value and str(value["segment_id"] or "") != replica["segment_id"]:
        return False
    if "role" in value and normalize_name(str(value["role"] or "")) != normalize_name(replica["role"]):
        return False
    if "role_index" in value:
        try:
            if value["role_index"] is None or int(value["role_index"]) != replica["role_index"]:
                return False
        except (TypeError, ValueError, OverflowError):
            return False
    return True


def narrator_audit_session_markers(db, chapter_id: str, tracks) -> tuple[list[SessionMarker], int]:
    """Только подтверждённые находки актуального дубля и актуального сценария.

    Счётчик включает подтверждённые находки, которые не удалось разместить.
    Он попадает в обычное предупреждение о пропущенных маркерах главы.
    """
    from app.services.asr_audit import narrator_lines
    from app.services.asr_audit_lifecycle import current_audit
    from app.services.asr_run import chapter_replicas

    audit = current_audit(db, chapter_id)
    if audit is None:
        return [], 0
    try:
        payload = json.loads(audit.findings_json or "{}")
        flags = payload.get("flags", [])
        if not isinstance(flags, list):
            raise ValueError("flags_not_list")
    except (ValueError, TypeError, AttributeError):
        logger.warning("Аудит %s: повреждены находки, метки не построены", audit.id)
        return [], 0
    confirmed = [flag for flag in flags if isinstance(flag, dict) and flag.get("confirmed") is True]
    if not confirmed:
        return [], 0
    current = {line["index"]: line for line in narrator_lines(db, chapter_id)["required"]}
    replicas = [replica for replica in chapter_replicas(db, chapter_id) if is_narrator_name(replica["role"])]
    narrator_clips = [(clip, clip.role or track.name) for track in tracks if is_narrator_name(track.name)
                      for clip in track.clips]
    markers: list[SessionMarker] = []
    skipped = 0
    for flag in confirmed:
        flag_source_id = str(flag.get("source_audio_file_id") or flag.get("audio_file_id")
                             or audit.audio_file_id or "")
        try:
            index = int(flag.get("index"))
            line = current.get(index)
            replica = replicas[index] if 0 <= index < len(replicas) else None
        except (TypeError, ValueError, OverflowError):
            line, replica = None, None
        if (line is None or replica is None or not _address_matches(flag, replica)
                or str(flag.get("segment_id") or "") != line["segment_id"]
                or str(flag.get("text") or "").split() != str(line["text"]).split()):
            skipped += 1
            logger.warning("Аудит %s: сценарий находки %s изменён, метка пропущена", audit.id, flag.get("index"))
            continue
        word_anchors = flag.get("word_anchors")
        anchors = word_anchors if isinstance(word_anchors, list) and word_anchors else [flag.get("anchor")]
        placed = False
        incomplete = False
        for number, anchor in enumerate(anchors):
            bounds = _bounds(anchor)
            if bounds is None:
                incomplete = True
                continue
            if anchors is word_anchors and not anchor.get("source_audio_file_id"):
                # Синтетическое время склеенного аудита годится только после
                # обратной привязки к исходнику; случайное совпадение чисел опасно.
                incomplete = True
                continue
            start, end = bounds
            if not _address_matches(anchor, replica):
                incomplete = True
                continue
            source_id = str(anchor.get("source_audio_file_id") or anchor.get("audio_file_id") or flag_source_id)
            clips = [clip for clip, role in narrator_clips if clip.audio_file_id == source_id
                     and clip.segment_id == replica["segment_id"]
                     and normalize_name(role) == normalize_name(replica["role"])
                     and (clip.role_index is None or clip.role_index == replica["role_index"])]
            # Окно строки помогает услышать пропуск, но не доказывает его позицию.
            # Точное место разрешено только отдельному словному якорю.
            approximate = anchors is not word_anchors or anchor.get("approximate") is not False
            matching = [clip for clip in clips if clip.source_in <= start < clip.source_out
                        and end <= clip.source_out]
            # Старый клип без индекса можно связать только с единственным куском
            # этой роли в абзаце. Совпавшее время не различает соседние реплики.
            same_address = [row for row in replicas if row["segment_id"] == replica["segment_id"]
                            and normalize_name(row["role"]) == normalize_name(replica["role"])]
            matching = [clip for clip in matching if clip.role_index is not None
                        or (len(same_address) == 1 and len(matching) == 1)]
            if not matching:
                incomplete = True
                continue
            words = flag.get("missing_words")
            words = words if isinstance(words, list) else []
            word = str(anchor.get("word") or " ".join(str(word) for word in words) or "проверить текст")
            precision = "приблизительно, у соседнего слова" if approximate else "точное место"
            for clip in matching:
                project_start = clip.start + start - clip.source_in
                key = f"noname-narrator:{chapter_id}:{source_id}:{flag.get('index')}:{number}:{project_start}:{word}"
                markers.append(SessionMarker(
                    name=f"РАССКАЗЧИК · пропуск «{word}» · {precision}",
                    start=project_start,
                    duration=0.0 if approximate else end - start,
                    comment=f"{precision}. Ожидается: {flag.get('text', '')}. "
                            f"{flag.get('note', '')} {flag.get('confirm_note', '')}".strip(),
                    guid=f"xmp:id:{uuid5(NAMESPACE_URL, key)}",
                ))
            placed = True
        if not placed or incomplete:
            skipped += 1
            logger.warning("Аудит %s: у находки %s нет надёжного места в клипах; метки пропущены%s",
                           audit.id, flag.get("index"), " частично" if placed else "")
    return markers, skipped
