"""Находки консилиума: запись прогона и чтение по книге.

Повторный прогон — обычное дело: правила сведения меняются, чтецы перечитывают книгу.
Поэтому запись идёт по месту (книга + абзац + отрезок), а не по прогону: находка о том же
месте остаётся той же находкой. И решение человека сильнее нового прогона — иначе каждый
перезапуск возвращал бы владельцу то, что он уже разобрал, и список перестал бы убывать.
"""
from __future__ import annotations

from app.models import ConsiliumFinding
from app.time_utils import iso_utc

KINDS = ("wrong_voice", "identity_play", "narrator_border", "readers_split")
STATUSES = ("new", "accepted", "dismissed", "gone")


def _key(row) -> tuple:
    return (int(row.chapter_index or 0), int(row.ordinal or 0),
            int(row.span_start or 0), int(row.span_end or 0))


def save_findings(db, *, book_id: str, run_label: str, findings: list[dict]) -> dict:
    book_id = str(book_id or "").strip()
    existing = {
        _key(row): row
        for row in db.query(ConsiliumFinding).filter(ConsiliumFinding.book_id == book_id).all()
    }
    added = 0
    for item in findings:
        # Род находки решает, как строку показывать и можно ли её принять. Опечатка в
        # нём осела бы в базе молча и вылезла бы уже в интерфейсе: список закрытый.
        kind = str(item.get("kind") or "")
        if kind not in KINDS:
            raise ValueError(f"unknown_kind:{kind}")
        key = (int(item.get("chapter_index") or 0), int(item.get("ordinal") or 0),
               int(item.get("span_start") or 0), int(item.get("span_end") or 0))
        if key in existing:
            continue
        db.add(ConsiliumFinding(book_id=book_id, run_label=str(run_label or ""), **item))
        added += 1
    db.flush()
    return {"added": added, "kept": len(existing)}


# Поля строки, которые прогон вправе переписать у нерешённой находки. Решение человека
# (status/decided_*) и место (ключ) — не из их числа.
_RUN_FIELDS = ("segment_id", "kind", "current_speaker", "readers_speaker", "reader_opus",
               "reader_sol", "arbiter_verdict", "arbiter_speaker", "evidence_para",
               "evidence_quote", "evidence_proven", "reason")


def apply_run(db, *, book_id: str, run_label: str, rows: list[dict], gone_keys: list[tuple]) -> dict:
    """Итог прогона: новые места — добавить, нерешённые и снятые — обновить, исчезнувшие — снять.

    `accepted`/`dismissed` не меняет ничто: иначе каждый перезапуск возвращал бы владельцу то,
    что он уже разобрал. Снятая находка, чьё место снова спорное, возвращается в новые.
    """
    book_id = str(book_id or "").strip()
    existing = {
        _key(row): row
        for row in db.query(ConsiliumFinding).filter(ConsiliumFinding.book_id == book_id).all()
    }
    added = updated = gone = 0
    for item in rows:
        kind = str(item.get("kind") or "")
        if kind not in KINDS:
            raise ValueError(f"unknown_kind:{kind}")
        key = (int(item.get("chapter_index") or 0), int(item.get("ordinal") or 0),
               int(item.get("span_start") or 0), int(item.get("span_end") or 0))
        row = existing.get(key)
        if row is None:
            db.add(ConsiliumFinding(book_id=book_id, run_label=str(run_label or ""), **item))
            added += 1
            continue
        if str(row.status) not in ("new", "gone"):
            continue
        # Запись условная: человек мог решить находку между чтением строк и этой записью
        # (прогон идёт в воркере, решения — в вебе). Безусловная запись вернула бы её в новые.
        values = {field: item[field] for field in _RUN_FIELDS if field in item}
        values.update(status="new", run_label=str(run_label or ""))
        if _guarded_update(db, row, ("new", "gone"), values):
            updated += 1
    for key in gone_keys:
        row = existing.get(tuple(int(part) for part in key))
        if row is not None and str(row.status) == "new" and _guarded_update(db, row, ("new",), {"status": "gone"}):
            gone += 1
    db.flush()
    return {"added": added, "updated": updated, "gone": gone}


def _guarded_update(db, row, statuses: tuple[str, ...], values: dict) -> bool:
    """Обновить строку, только если её статус в базе всё ещё из `statuses`."""
    written = (db.query(ConsiliumFinding)
               .filter(ConsiliumFinding.id == row.id, ConsiliumFinding.status.in_(statuses))
               .update(values, synchronize_session=False))
    db.expire(row)  # строка в сессии не должна показывать ни старое, ни несостоявшееся
    return written == 1


EXCERPT_CHARS = 140


def _excerpt(text: str) -> str:
    """Начало абзаца для строки списка — по границе слова, а не посреди него."""
    flat = " ".join(str(text or "").split())
    if len(flat) <= EXCERPT_CHARS:
        return flat
    cut = flat.rfind(" ", 0, EXCERPT_CHARS)
    return flat[: cut if cut > 0 else EXCERPT_CHARS].rstrip(" ,.;:—-") + "…"


def book_findings(db, *, book_id: str, chapter_index: int | None = None) -> dict:
    from app.v2.models import V2Segment

    query = db.query(ConsiliumFinding).filter(ConsiliumFinding.book_id == str(book_id or "").strip())
    if chapter_index is not None:
        query = query.filter(ConsiliumFinding.chapter_index == int(chapter_index))
    rows = query.order_by(ConsiliumFinding.chapter_index.asc(), ConsiliumFinding.ordinal.asc()).all()
    segments = {
        seg.id: seg for seg in db.query(V2Segment)
        .filter(V2Segment.id.in_([row.segment_id for row in rows])).all()
    } if rows else {}
    counts = {kind: 0 for kind in KINDS} | {status: 0 for status in STATUSES}
    new_by_kind = {kind: 0 for kind in KINDS}
    items = []
    for row in rows:
        counts[str(row.kind)] = counts.get(str(row.kind), 0) + 1
        counts[str(row.status)] = counts.get(str(row.status), 0) + 1
        if str(row.status) == "new":
            new_by_kind[str(row.kind)] = new_by_kind.get(str(row.kind), 0) + 1
        segment = segments.get(row.segment_id)
        items.append({
            "id": row.id, "chapter_index": row.chapter_index, "ordinal": row.ordinal,
            "chapter_id": str(segment.chapter_id) if segment is not None else "",
            "segment_id": row.segment_id, "span_start": row.span_start, "span_end": row.span_end,
            "excerpt": _excerpt(segment.text[int(row.span_start):int(row.span_end)])
                       if segment is not None else "",
            "kind": row.kind, "current_speaker": row.current_speaker,
            "readers_speaker": row.readers_speaker,
            "reader_opus": row.reader_opus, "reader_sol": row.reader_sol,
            "arbiter_verdict": row.arbiter_verdict,
            "arbiter_speaker": row.arbiter_speaker, "evidence_para": row.evidence_para,
            "evidence_quote": row.evidence_quote, "evidence_proven": bool(row.evidence_proven),
            "reason": row.reason, "status": row.status, "decided_by": row.decided_by,
            "decided_at": iso_utc(row.decided_at) or "",
            "decided_speaker": row.decided_speaker,
        })
    counts["new_by_kind"] = new_by_kind
    return {"counts": counts, "items": items}


def _decide(db, finding_id: str, status: str, actor_uid: str, actor_name: str,
            speaker: str) -> ConsiliumFinding:
    from app.time_utils import utcnow_naive

    row = db.get(ConsiliumFinding, str(finding_id or "").strip())
    if row is None:
        raise ValueError("finding_not_found")
    # Второе решение поверх первого соврало бы в списке: «оставлено как есть» при уже
    # изменённой разметке. Передумать — это обычный выбор роли, а не эта кнопка.
    # Снятая находка тоже не подлежит ручному решению: её место исчезло из прогона.
    if str(row.status or "") not in ("new",):
        raise ValueError("finding_already_decided")
    row.status = status
    row.decided_by = str(actor_name or actor_uid or "")
    row.decided_at = utcnow_naive()
    row.decided_speaker = str(speaker or "")
    db.flush()
    return row


def accept_finding(db, *, finding_id: str, actor_uid: str, actor_name: str = "",
                   speaker: str | None = None) -> dict:
    """Сменить отвергнутую роль в абзаце — переписав ВЕСЬ абзац новой версией.

    Пишет не своим запросом, а `reassign_segment`: там правило действующей версии уже
    соблюдено (версия ведётся по абзацу, и одиночная вставка убила бы соседние реплики),
    там же пишется запись в журнал вмешательств. Источник правки — `operator`, и это
    честно: решение принял человек, а происхождение видно в журнале.

    `speaker` — имя, которое назвал человек. Оно сильнее блока «арбитр не доказал»:
    машина не вправе поставить недоказанное, человек — вправе. Без него действует прежний
    контракт: применяется только доказанный `change`.
    """
    from app.v2.attribution_ops import NARRATOR, _cast_names, reassign_segment
    from app.v2.reader import effective_attributions

    row = db.get(ConsiliumFinding, str(finding_id or "").strip())
    if row is None:
        raise ValueError("finding_not_found")
    # Решение уже принято — а список у человека мог остаться старым (вторая вкладка).
    # Повторный приём вернул бы подсказку машины поверх того, что человек сделал после.
    if str(row.status or "") != "new":
        raise ValueError("finding_already_decided")

    if speaker is not None:
        # Имя от человека: только роль каста этой книги или рассказчик. `UNSURE` — не
        # решение, а отказ его принимать, и в разметку он попадать не должен.
        wanted = str(speaker or "").strip()
        new_speaker = NARRATOR if wanted == NARRATOR else _cast_names(db, row.book_id).get(wanted.lower(), "")
        if not new_speaker:
            raise ValueError("unknown_speaker")
        # «Оставить как в сценарии» — не новая версия абзаца с теми же ролями, а отметка.
        if new_speaker == str(row.current_speaker or ""):
            _decide(db, finding_id, "dismissed", actor_uid, actor_name, new_speaker)
            return {"ok": True, "segment_id": row.segment_id, "speaker": new_speaker,
                    "status": "dismissed"}
    else:
        # Пустое имя арбитра — это не всегда «нечего сказать»: при `keep_current` оно пусто
        # ШТАТНО, и откат на мнение чтецов через `or` в этом случае подставил бы роль, которую
        # арбитр только что отверг. Различать нужно по вердикту, а не по пустоте строки —
        # пустой вердикт (арбитраж не прогоняли вовсе) остаётся особым случаем, где откат на
        # чтецов и есть замысел.
        #
        # `undecidable` — тот же класс, что и `keep_current`: спека понижает до него всё, что
        # не прошло проверку цитатой, карточка говорит «арбитр не смог решить», и кнопка не
        # вправе тут же поставить либо мнение чтецов, либо имя, которое арбитр сам объявил
        # недоказанным. Пока человек не выберет сам — применять нечего.
        verdict = str(row.arbiter_verdict or "").strip()
        if verdict in ("keep_current", "undecidable"):
            raise ValueError(f"arbiter_says_{verdict}")
        new_speaker = str(row.arbiter_speaker or "").strip() or (
            str(row.readers_speaker or "").strip() if verdict != "change" else ""
        )
        if not new_speaker:
            raise ValueError("no_speaker_to_apply")

    existing = effective_attributions(db, [row.segment_id]).get(row.segment_id, [])
    # Границы находки могли устареть — абзац успели переразметить между делом. Замена
    # «вслепую» без совпадения границ записала бы новую версию с теми же ролями:
    # находка пометилась бы принятой, а голос остался бы прежним — и человек узнал бы об
    # этом только в студии.
    hits = [item for item in existing
            if (int(item.span_start), int(item.span_end)) == (int(row.span_start), int(row.span_end))]
    if not hits:
        raise ValueError("span_not_in_markup")

    # Меняем не отрезок, а ГОЛОС В АБЗАЦЕ. Реплика героя сплошь и рядом разорвана
    # ремаркой и лежит двумя отрезками одного и того же персонажа (2788 абзацев из
    # 13 276 — каждый пятый). Находка цепляется к самой длинной половине; переименовать
    # одну её значит отдать одну фразу одного героя двум актёрам, и всплывёт это уже
    # у микрофона. Поэтому имя меняется у ВСЕХ отрезков абзаца, где стоит отвергнутая
    # роль, а совпадение границ остаётся сторожем устаревания находки.
    # Отрезки других персонажей не трогаются: в книге есть абзац, где звучат двое
    # разных, и «переименовать всё подряд» было бы той же ошибкой наоборот.
    #
    # Рассказчик — исключение из этого же правила, не его частный случай. Правило
    # целого абзаца существует ради разорванной РЕПЛИКИ ГЕРОЯ (ремарка режет её на
    # половины одного и того же персонажа). У рассказчика в абзаце сплошь и рядом
    # два НЕЗАВИСИМЫХ отрезка — например песня и следом за ней ремарка «— сказал
    # он»: оба стоят как Рассказчик, но это не половины одной реплики. Находка о
    # рассказчике поэтому трогает только свой собственный отрезок.
    targets = (
        [item for item in hits if str(item.speaker) == NARRATOR]
        if str(row.current_speaker or "") == NARRATOR
        else [item for item in existing if str(item.speaker) == str(row.current_speaker or "")]
    )
    # Границы те же, а имя на них уже другое: место правили руками после прогона.
    # Такие правки идут параллельно с разбором находок, и подсказка машины не должна
    # ложиться поверх решения человека — пусть он посмотрит на место заново.
    if not any(item in targets for item in hits):
        raise ValueError("speaker_already_changed")

    spans = [
        {
            "start": int(item.span_start),
            "end": int(item.span_end),
            "speaker": new_speaker if item in targets else str(item.speaker),
            # 1.0 — только отрезкам, чью роль человек только что подтвердил. Соседей
            # трогать нельзя: их уверенность модели уходит в читалку, и затирать её
            # единицей — тихая потеря данных.
            "confidence": 1.0 if item in targets else float(item.confidence or 0.0),
        }
        for item in existing
    ]
    reassign_segment(db, segment_id=row.segment_id, spans=spans,
                     actor_uid=actor_uid, actor_name=actor_name,
                     # Соседи, чью роль никто не менял, сохраняют прежний источник: ложная
                     # метка оператора вывела бы их из-под слоя доказательств.
                     keep_source={(int(item.span_start), int(item.span_end)): str(item.source)
                                  for item in existing if item not in targets})
    _decide(db, finding_id, "accepted", actor_uid, actor_name, new_speaker)
    return {"ok": True, "segment_id": row.segment_id, "speaker": new_speaker, "status": "accepted"}


def dismiss_finding(db, *, finding_id: str, actor_uid: str, actor_name: str = "") -> dict:
    """Оставить как есть. Разметка не трогается вовсе — только отметка о решении."""
    row = db.get(ConsiliumFinding, str(finding_id or "").strip())
    if row is None:
        raise ValueError("finding_not_found")
    _decide(db, finding_id, "dismissed", actor_uid, actor_name, str(row.current_speaker or ""))
    return {"ok": True, "finding_id": row.id}
