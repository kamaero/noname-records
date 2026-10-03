"""Жёсткий гейт честности слоя 1: не доказывает ли он то, что человек уже исправил.

Смысл проверки (спека «Арбитр разметки», раздел «Калибровка»): у нас есть 750 мест,
где ответ известен — оператор вручную сменил персонажа. Если слой 1, глядя на СТАРУЮ
(исправленную) разметку, находит «доказательство» в её пользу, значит он доказывает
неверное, и на его вердикт нельзя опираться нигде.

Проверка бесплатная и только на чтение: ни строки в базу, ни обращения к модели.
Повторяет разбор на старой версии абзаца, подставляя её вместо действующей, — поэтому
живёт рядом со слоем 1, а не в скрипте: логика должна проверяться тестами.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

from app.models import Character, ScriptChapter
from app.pipeline.attribution_evidence import NARRATOR, PROVEN_KINDS, classify_chapter
from app.pipeline.attribution_resolve import build_cast_index
from app.v2.models import V2Attribution
from app.v2.reader import effective_attributions
from app.v2.store import load_chapter_segments

OPERATOR = "operator"


@dataclass
class Violation:
    """Одно ложное доказательство: слой 1 подпёр ярлык, который оператор убрал."""

    chapter_index: int
    paragraph: int
    kind: str
    old_speaker: str
    new_speakers: list[str] = field(default_factory=list)
    quote: str = ""
    text: str = ""


def _cast_index(db, book_id: str) -> dict[str, str]:
    cast = db.query(Character).filter(Character.book_id == book_id).all()
    return build_cast_index([(row.name, [a.strip() for a in (row.aliases or "").split(",")
                                         if a.strip()]) for row in cast])


def _rows_by_segment(db, segment_ids: list[str]) -> dict[str, list[V2Attribution]]:
    """ВСЕ версии отрезков, а не только действующая: гейт сравнивает две соседние."""
    out: dict[str, list[V2Attribution]] = collections.defaultdict(list)
    for chunk in [segment_ids[i:i + 400] for i in range(0, len(segment_ids), 400)]:
        for row in db.query(V2Attribution).filter(V2Attribution.segment_id.in_(chunk)).all():
            out[row.segment_id].append(row)
    return out


def _corrected_spans(rows: list[V2Attribution]) -> tuple[list, list, list]:
    """Отрезки предыдущей версии, у которых оператор сменил персонажа.

    Возвращает `(старая версия целиком, исправленные отрезки, новая версия)`. Смена
    границ без смены персонажа исправлением не считается: ярлык остался тем же, и
    доказывать его слою 1 не запрещено.
    """
    versions = {int(row.version or 0) for row in rows}
    if len(versions) < 2:
        return [], [], []
    newest = max(versions)
    new = [row for row in rows if int(row.version or 0) == newest]
    if not any(str(row.source or "") == OPERATOR for row in new):
        return [], [], []
    previous = max(version for version in versions if version < newest)
    old = [row for row in rows if int(row.version or 0) == previous]
    new_keys = {(int(r.span_start), int(r.span_end), str(r.speaker or "").strip()) for r in new}
    corrected = []
    for row in old:
        speaker = str(row.speaker or "").strip()
        if speaker == NARRATOR:
            continue
        if (int(row.span_start), int(row.span_end), speaker) in new_keys:
            continue
        overlapping = [str(n.speaker or "").strip() for n in new
                       if not (int(n.span_end) <= int(row.span_start)
                               or int(n.span_start) >= int(row.span_end))]
        if speaker in overlapping:
            continue
        corrected.append((row, overlapping))
    return old, corrected, new


def check_gate(db, *, book_id: str) -> dict:
    """Прогнать гейт по книге. `violations` пуст — слой 1 честен на известных ответах."""
    names = _cast_index(db, book_id)
    chapters = (db.query(ScriptChapter)
                .filter(ScriptChapter.book_id == book_id)
                .order_by(ScriptChapter.chapter_index.asc()).all())

    checked = 0
    violations: list[Violation] = []
    for chapter in chapters:
        segments = load_chapter_segments(db, chapter_id=str(chapter.id))
        if not segments:
            continue
        segment_ids = [segment.id for segment in segments]
        effective = effective_attributions(db, segment_ids)
        every_version = _rows_by_segment(db, segment_ids)
        base = [{"paragraph": segment.ordinal, "text": segment.text,
                 "spans": [{"speaker": row.speaker, "start": row.span_start,
                            "end": row.span_end}
                           for row in effective.get(segment.id, [])]}
                for segment in segments]
        position = {segment.id: index for index, segment in enumerate(segments)}

        for segment in segments:
            old, corrected, _ = _corrected_spans(every_version.get(segment.id) or [])
            if not corrected:
                continue
            checked += len(corrected)
            # Подменяем только этот абзац: соседи остаются в действующей разметке,
            # иначе мы мерили бы не слой 1, а согласованность целой старой главы.
            paragraphs = [dict(item) for item in base]
            paragraphs[position[segment.id]] = {
                "paragraph": segment.ordinal, "text": segment.text,
                "spans": [{"speaker": row.speaker, "start": row.span_start,
                           "end": row.span_end} for row in old]}
            evidence = classify_chapter(paragraphs, names=names)
            for row, overlapping in corrected:
                key = (int(segment.ordinal), int(row.span_start), int(row.span_end))
                found = evidence.get(key)
                if found is None or found.kind not in PROVEN_KINDS:
                    continue
                violations.append(Violation(
                    chapter_index=int(chapter.chapter_index or 0),
                    paragraph=int(segment.ordinal or 0),
                    kind=found.kind,
                    old_speaker=str(row.speaker or "").strip(),
                    new_speakers=overlapping,
                    quote=str(found.quote or "")[:160],
                    text=str(segment.text or "")[int(row.span_start):int(row.span_end)][:120],
                ))

    by_kind = collections.Counter(item.kind for item in violations)
    to_narrator = [item for item in violations
                   if item.new_speakers and set(item.new_speakers) == {NARRATOR}]
    return {"book_id": book_id, "checked": checked, "violations": violations,
            "by_kind": dict(by_kind),
            "to_narrator": len(to_narrator),
            "to_other_character": len(violations) - len(to_narrator)}


def render_gate(result: dict, *, limit: int = 25) -> str:
    """Сводка для человека: сколько исправлений проверено и где слой 1 солгал."""
    violations = result["violations"]
    checked = int(result["checked"] or 0)
    share = (len(violations) / checked * 100) if checked else 0.0
    lines = [
        f"отрезков, где оператор сменил персонажа: {checked}",
        f"из них слой 1 доказывает СТАРЫЙ (исправленный) ярлык: {len(violations)} ({share:.1f} %)",
        f"  по классам: {result['by_kind'] or '{}'}",
        f"  исправление было «персонаж → Рассказчик»: {result['to_narrator']}",
        f"  исправление было на ДРУГОГО персонажа: {result['to_other_character']}",
    ]
    if violations and limit > 0:
        lines += ["", "примеры:"]
        for item in violations[:limit]:
            lines.append(
                f"  гл.{item.chapter_index} абз.{item.paragraph} {item.kind}: "
                f"доказывал «{item.old_speaker}», а там {item.new_speakers}")
            lines.append(f"      цитата: {item.quote}")
            lines.append(f"      реплика: {item.text}")
    return "\n".join(lines)
