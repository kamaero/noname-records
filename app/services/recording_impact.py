"""Что станет со звуком реплики, если отдать её другой роли, — чтобы человек знал до клика.

Правило то же, что у поиска у соседних ролей (`asr_borrow`): тот же актёр — звук берётся
из соседнего файла; другой — честный пропуск и письмо новому актёру. Здесь только расчёт
и текст; ничего не пишется.

Имя роли в текстах не склоняется и стоит в кавычках: «у Гамука» из «Гамук» машина
угадает, а «у Хора», «у Безымянного алхимика» — нет.
"""
from __future__ import annotations

from app.models import AudioFile, Character, ScriptChapter
from app.services.asr_borrow import chapter_done_jobs
from app.services.author_profile import normalize_name
from app.v2.cast_ops import approved_actor
from app.v2.models import V2Segment

INFO, WARN = "info", "warn"


def chapter_has_recognition(db, chapter_id: str) -> bool:
    return bool(chapter_done_jobs(db, chapter_id))


def _actor_label(name: str) -> str:
    return name or "актёр не назначен"


def _state_text(state: str, *, from_role: str, from_actor: str, to_role: str, to_actor: str) -> str:
    if state == "borrow":
        return f"Глава записана. Звук возьмём из файла роли «{from_role}» — читает тот же актёр ({from_actor})."
    if state == "borrow_no_file":
        return (f"Глава записана, звук лежит у роли «{from_role}» (тот же актёр), но у роли «{to_role}» в этой главе "
                "нет своей записи — сборка его не возьмёт, реплику придётся дописать.")
    if state == "rerecord":
        tail = "ему придёт уведомление." if to_actor else "уведомлять некого — назначьте актёра."
        return (f"Глава записана — реплика лежит у роли «{from_role}» ({_actor_label(from_actor)}). После правки её "
                f"дозапишет «{to_role}» ({_actor_label(to_actor)}), {tail}")
    if state == "not_in_audio":
        tail = "придёт уведомление её дописать." if to_actor else "— уведомлять некого, назначьте актёра."
        return f"Глава записана, но этой реплики в записи нет. Актёру роли «{to_role}» ({_actor_label(to_actor)}) {tail}"
    if state == "new_role_unrecorded":
        return f"У роли «{to_role}» в этой главе ещё нет записи — реплика войдёт в её обычную запись."
    return ""


_LEVEL = {"not_recorded": INFO, "borrow": INFO, "new_role_unrecorded": INFO,
          "borrow_no_file": WARN, "rerecord": WARN, "not_in_audio": WARN}


def _context(db, segment_id: str, span_start: int, span_end: int) -> dict | None:
    """Всё, что не зависит от роли-получателя: глава, реплика, её звук у текущей роли."""
    from app.services.asr_run import chapter_replicas

    segment = db.get(V2Segment, str(segment_id or "").strip())
    if segment is None:
        return None
    jobs = chapter_done_jobs(db, segment.chapter_id)
    if not jobs:
        # Незаписанная глава — большинство правок: реплики главы не читаем вовсе.
        return {"recorded": False, "from_role": "", "found": False, "heard_actor": "", "files": set(), "cast": {}}
    chapter = db.get(ScriptChapter, segment.chapter_id)
    replica = next((item for item in chapter_replicas(db, segment.chapter_id)
                    if item["segment_id"] == segment.id
                    and (item["span_start"], item["span_end"]) == (int(span_start), int(span_end))), None)
    found, heard_actor = False, ""
    from_role = str(replica["role"]) if replica else ""
    if replica:
        for job, audio, alignment in jobs:
            if str(job.expected_role or "").strip() != from_role:
                continue
            lines = alignment.get("lines") or []
            index = int(replica["role_index"])
            if index < len(lines) and lines[index].get("matched"):
                source = str(lines[index].get("source_audio_file_id") or "")
                owner = db.get(AudioFile, source) if source else audio
                found, heard_actor = True, str(getattr(owner, "actor_name", "") or "")
                break
    files = {
        str(role or "").strip()
        for (role,) in db.query(AudioFile.role).filter(
            AudioFile.kind == "take", AudioFile.chapter == str(getattr(chapter, "chapter_title", "") or "")).all()
    }
    cast = _cast_actors_for_impact(db, segment.book_id)
    return {"recorded": bool(jobs), "from_role": from_role, "found": found, "heard_actor": heard_actor,
            "files": files, "cast": cast}


def _cast_actors_for_impact(db, book_id: str) -> dict[str, str]:
    """Роль → утверждённый актёр, включая рассказчика.

    Рассказчик — не строка каста с именем «Рассказчик» по умолчанию: его актёр решается
    отдельным правилом (`app.v2.reader._narrator_actor` — своя запись Character, затем
    `narrator_role`, затем `BookBudget`). Без этого переезд реплики на «Рассказчика» в
    записанной главе всегда видит пустого актёра и врёт «уведомлять некого».
    """
    from app.v2.reader import NARRATOR, _narrator_actor

    characters = db.query(Character).filter(Character.book_id == book_id).all()
    cast = {
        str(character.name or "").strip(): approved_actor(character.actor_name)
        for character in characters
    }
    cast[NARRATOR] = approved_actor(_narrator_actor(db, book_id, characters))
    return cast


def _state(ctx: dict, to_role: str) -> dict:
    if not ctx["recorded"]:
        return {"state": "not_recorded", "level": INFO, "text": ""}
    to_actor = ctx["cast"].get(to_role, "")
    to_has_file = to_role in ctx["files"]
    same = ctx["found"] and bool(to_actor) and normalize_name(ctx["heard_actor"]) == normalize_name(to_actor)
    if same:
        state = "borrow" if to_has_file else "borrow_no_file"
    elif not to_has_file:
        state = "new_role_unrecorded"
    elif ctx["found"]:
        state = "rerecord"
    else:
        state = "not_in_audio"
    text = _state_text(state, from_role=ctx["from_role"], from_actor=ctx["heard_actor"],
                       to_role=to_role, to_actor=to_actor)
    return {"state": state, "level": _LEVEL[state], "text": text}


def recording_impacts(db, *, segment_id: str, span_start: int, span_end: int, to_roles: list[str]) -> dict:
    ctx = _context(db, segment_id, span_start, span_end)
    if ctx is None:
        return {"recorded": False, "by_speaker": {}}
    return {"recorded": ctx["recorded"],
            "by_speaker": {role: _state(ctx, role) for role in to_roles if role != ctx["from_role"]}}


def impacts_for_reassign(db, *, segment_id: str, spans) -> list[dict]:
    """Предупреждения для ручной правки: считаются ДО записи новой версии абзаца.

    Имя роли в `spans` — то, что напечатал клиент, тем же путём канонизирует
    `reassign_segment` (`_cast_names(...).get(speaker.lower())`, рассказчик и UNSURE —
    как есть). Без этого «гамук» и «Гамук» — разные роли для сравнения `old`/`new`, а
    неизвестное имя тихо становится ролью-получателем, хотя запись его тут же отклонит.
    """
    from app.v2.attribution_ops import NARRATOR, UNSURE, _cast_names
    from app.v2.reader import effective_attributions

    segment_id = str(segment_id or "").strip()
    segment = db.get(V2Segment, segment_id)
    # Сначала — есть ли у главы распознавание: без него предупреждать не о чем, и ручная
    # правка незаписанной главы не платит ни за разметку, ни за реплики главы.
    if segment is None or not chapter_has_recognition(db, segment.chapter_id):
        return []
    existing = effective_attributions(db, [segment_id]).get(segment_id, [])
    if not existing:
        return []
    cast_names = _cast_names(db, segment.book_id)

    def canonical(speaker) -> str:
        name = str(speaker or "").strip()
        if name in (NARRATOR, UNSURE):
            return name
        return cast_names.get(name.lower(), "")

    out = []
    for row in existing:
        start, end, old = int(row.span_start), int(row.span_end), str(row.speaker or "")
        overlapping = [span for span in spans or []
                       if int(span.get("start", 0)) < end and int(span.get("end", 0)) > start]
        new = next((candidate for candidate in (canonical(span.get("speaker")) for span in overlapping)
                    if candidate and candidate != old), "")
        if not new:
            continue
        ctx = _context(db, segment_id, start, end)
        if ctx is None or not ctx["recorded"]:
            continue
        out.append({"span_start": start, "span_end": end, "from_role": old, "to_role": new, **_state(ctx, new)})
    return out
