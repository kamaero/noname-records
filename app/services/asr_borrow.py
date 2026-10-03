"""Реплику отдали другой роли того же актёра — её звук уже лежит в соседнем файле.

Сверка идёт по роли: реплики роли ищутся в файлах роли. После правки разметки в
записанной главе это врёт — реплика, перенесённая от Тупуга к Гамуку, прочитана в файле
Тупуга, а для Гамука числится пропущенной, и архив главы стоит.

Шаг идёт поверх уже посчитанной сверки всех файлов главы и помечает строку получателя
найденной, с `source_audio_file_id` донора. Признак лежит в строке получателя, а не
донора: пропуски считают статус главы, отчёт владельцу, блок архива и письма — все по
`matched` в джобе роли, и помеченная там строка чинит их без правок. Файл нужен только
сборке сессии.

Донор — только тот же актёр: чужой голос в роль не подставляется (решение владельца
2026-09-15). Позиции реплики внутри чужого файла мы не знаем, поэтому поиск идёт по всему
свободному потоку и спрашивает строго — `RESYNC_THRESHOLD`, как дальний поиск сверки.
"""
from __future__ import annotations

import json

from app.services.asr_align import MIN_TAKE_WORDS, RESYNC_THRESHOLD, _best_span, _collect_takes, normalize_words, word_stream
from app.services.asr_run import store_alignment
from app.services.author_profile import normalize_name
from app.v2.cast_ops import approved_actor


def chapter_done_jobs(db, chapter_id: str) -> list[tuple]:
    """Посчитанные джобы главы с их файлами и разобранной сверкой, в порядке загрузки."""
    from app.models import AsrJob, AudioFile

    rows = (
        db.query(AsrJob, AudioFile)
        .join(AudioFile, AudioFile.id == AsrJob.audio_file_id)
        .filter(AsrJob.chapter_id == str(chapter_id or "").strip(), AsrJob.status == "done")
        .order_by(AudioFile.uploaded_at.asc())
        .all()
    )
    out = []
    for job, audio in rows:
        try:
            alignment = json.loads(job.alignment_json or "{}")
        except ValueError:
            continue
        if alignment.get("lines") is not None:
            out.append((job, audio, alignment))
    return out


def _unheard_indices(jobs) -> dict[str, set[int]]:
    """Роль → номера строк, не найденных ни в одном её файле."""
    by_role: dict[str, list[dict]] = {}
    for job, _audio, alignment in jobs:
        by_role.setdefault(str(job.expected_role or "").strip(), []).append(alignment)
    out: dict[str, set[int]] = {}
    for role, alignments in by_role.items():
        sizes = {len(item.get("lines") or []) for item in alignments}
        count = min(sizes) if sizes else 0
        out[role] = {
            index for index in range(count)
            if not any((item["lines"][index] or {}).get("matched") for item in alignments)
        }
    return out


def role_unheard_texts(db, chapter_id: str) -> dict[str, list[str]]:
    """Роль → тексты строк, которых нет ни в одном её файле, в порядке сценария."""
    jobs = chapter_done_jobs(db, chapter_id)
    first_alignment: dict[str, dict] = {}
    for job, _audio, alignment in jobs:
        first_alignment.setdefault(str(job.expected_role or "").strip(), alignment)
    return {
        role: [str(first_alignment[role]["lines"][index].get("text") or "") for index in sorted(indices)]
        for role, indices in _unheard_indices(jobs).items()
        if indices
    }


def _cast_actors(db, chapter_id: str) -> dict[str, str]:
    """Роль → нормализованный утверждённый актёр, включая рассказчика.

    У рассказчика нет своего актёра по умолчанию: он решается отдельным правилом
    (`app.v2.reader._narrator_actor`), которое смотрит на запись Character «Рассказчик»,
    затем на `narrator_role`, затем на `BookBudget`. Джобы главы бывают и его тоже — без
    этой записи заимствование никогда не находило донора/получателя среди строк рассказчика.
    """
    from app.models import Character, ScriptChapter
    from app.v2.reader import NARRATOR, _narrator_actor

    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None:
        return {}
    characters = db.query(Character).filter(Character.book_id == chapter.book_id).all()
    actors = {
        str(character.name or "").strip(): normalize_name(approved_actor(character.actor_name))
        for character in characters
    }
    actors[NARRATOR] = normalize_name(approved_actor(_narrator_actor(db, chapter.book_id, characters)))
    return actors


def _taken_mask(stream, alignment: dict, borrowed_out: list[tuple[float, float]] = ()) -> list[bool]:
    """Слова, которые уже принадлежат найденным репликам самого файла.

    `borrowed_out` — отрезки ЭТОГО файла, уже отданные под чужую реплику при более
    раннем вызове (своей сверки файла они не касаются: там `source_audio_file_id` не
    его, а получателя). Без них повторный вызов не видит собственных прежних заимствований
    и находит для новой реплики то же самое место — см. `_borrowed_spans`.
    """
    spans = [
        (float(take["start"]), float(take["end"]))
        for line in alignment.get("lines") or [] if line.get("matched") and not line.get("source_audio_file_id")
        for take in line.get("takes") or [] if take.get("start") is not None and take.get("end") is not None
    ]
    spans = spans + list(borrowed_out)
    return [any(start < t_end and end > t_start for t_start, t_end in spans) for _w, start, end in stream]


def _borrowed_spans(jobs) -> dict[str, list[tuple[float, float]]]:
    """Аудио id донора → отрезки, которые он уже одолжил чужой реплике.

    Такой отрезок лежит в СТРОКЕ ПОЛУЧАТЕЛЯ (`source_audio_file_id` == этот донор), а не
    в собственной сверке донора, поэтому `_taken_mask` донора его не видит без отдельного
    прохода по всем джобам главы. Без этого прохода повторный вызов `borrow_across_roles`
    находит для новой ненайденной строки то же самое, уже отданное место.
    """
    out: dict[str, list[tuple[float, float]]] = {}
    role_of_audio = {str(audio.id): str(job.expected_role or "").strip() for job, audio, _al in jobs}
    for job, _audio, alignment in jobs:
        role = str(job.expected_role or "").strip()
        for line in alignment.get("lines") or []:
            donor_id = line.get("source_audio_file_id")
            if not donor_id or not line.get("matched"):
                continue
            # Копия из соседнего файла той же роли (`_copy_from_siblings`) — не заём у
            # чужой роли: эти слова уже заняты собственной строкой того файла.
            if role_of_audio.get(str(donor_id)) == role:
                continue
            for take in line.get("takes") or []:
                if take.get("start") is not None and take.get("end") is not None:
                    out.setdefault(str(donor_id), []).append((float(take["start"]), float(take["end"])))
    return out


def _free_runs(mask: list[bool]) -> list[tuple[int, int]]:
    """Непрерывные куски свободных слов: поиск не склеивает слова через чужую реплику."""
    runs, start = [], None
    for index, taken in enumerate(mask + [True]):
        if not taken and start is None:
            start = index
        elif taken and start is not None:
            runs.append((start, index))
            start = None
    return runs


def _recount(alignment: dict) -> None:
    lines = alignment.get("lines") or []
    alignment["missing"] = [line["index"] for line in lines if not line.get("matched")]
    alignment["matched"] = len(lines) - len(alignment["missing"])
    alignment["total"] = len(lines)
    alignment["coverage"] = alignment["matched"] / len(lines) if lines else 0.0


def _copy_from_siblings(jobs) -> set[str]:
    """Строка, найденная в одном файле роли, — найдена и в остальных её файлах.

    Роль бывает записана несколькими файлами: актёр дописал перенесённую к нему реплику
    отдельным дублем. Сверка идёт по файлу, и без этого шага старый файл числит
    дописанную реплику пропущенной, а новый — все остальные: диктору уходят ложные письма
    «не нашлось реплик», а пропуски главы (`book_recording_status`) не сходятся к нулю
    никогда, и архив стоит. Строка копируется со своими таймингами и
    `source_audio_file_id` того файла, где она звучит (или её собственного донора).

    Возвращает id джоб, чья сверка поменялась.
    """
    by_role: dict[str, list[tuple]] = {}
    for job, audio, alignment in jobs:
        by_role.setdefault(str(job.expected_role or "").strip(), []).append((job, audio, alignment))
    changed: set[str] = set()
    for siblings in by_role.values():
        if len(siblings) < 2:
            continue
        for job, audio, alignment in siblings:
            lines = alignment.get("lines") or []
            for index, line in enumerate(lines):
                if line.get("matched"):
                    continue
                for _other_job, other_audio, other_alignment in siblings:
                    if other_audio is audio:
                        continue
                    other_lines = other_alignment.get("lines") or []
                    if index >= len(other_lines):
                        continue
                    source_line = other_lines[index]
                    if not source_line.get("matched") or source_line.get("text") != line.get("text"):
                        continue
                    source_id = str(source_line.get("source_audio_file_id") or other_audio.id)
                    # Прежняя копия из этого же файла — не звук: сам файл строку уже не слышит.
                    if source_id == str(audio.id):
                        continue
                    line.update({
                        "matched": True, "score": source_line.get("score", 0.0),
                        "start": source_line.get("start"), "end": source_line.get("end"),
                        "takes": [dict(take) for take in source_line.get("takes") or []],
                        "source_audio_file_id": source_id,
                    })
                    changed.add(str(job.id))
                    break
    return changed


def borrow_across_roles(db, chapter_id: str) -> dict:
    """Найти звук ненайденных реплик в файлах других ролей того же актёра в главе.

    Вызывать поверх чистой сверки (после `realign_job`/`run_asr_for_take` всех джоб главы):
    уже заимствованная строка `matched`, и повторный вызов её не ищет.
    """
    jobs = chapter_done_jobs(db, chapter_id)
    actors = _cast_actors(db, chapter_id)
    # Сначала — свои файлы роли: реплика, дописанная отдельным дублем, не ищется у соседей.
    touched = _copy_from_siblings(jobs)
    unheard = _unheard_indices(jobs)
    borrowed_out = _borrowed_spans(jobs)

    donors = []
    for job, audio, alignment in jobs:
        try:
            heard = json.loads(job.heard_json or "{}")
        except ValueError:
            continue
        stream = word_stream(heard.get("segments") or [])
        if stream:
            mask = _taken_mask(stream, alignment, borrowed_out.get(str(audio.id), []))
            donors.append({"job": job, "audio": audio, "stream": stream, "mask": mask,
                           "actor": normalize_name(str(audio.actor_name or ""))})

    report = {"searched": 0, "borrowed": 0, "roles": {}}
    for job, _audio, alignment in jobs:
        role = str(job.expected_role or "").strip()
        wanted_actor = actors.get(role, "")
        indices = sorted(unheard.get(role, set()))
        if not wanted_actor or not indices:
            continue
        changed = False
        for index in indices:
            # У роли с несколькими файлами строку уже мог забрать её предыдущий файл.
            if index not in unheard.get(role, set()):
                continue
            line = alignment["lines"][index]
            target = normalize_words(str(line.get("text") or ""))
            # «Да.», «Нет.» по всему чужому потоку находятся где угодно: такой заём прятал
            # бы настоящий пропуск. Короткую реплику честно дописывают.
            if len(target) < MIN_TAKE_WORDS:
                continue
            report["searched"] += 1
            best = None
            for donor in donors:
                if donor["actor"] != wanted_actor or str(donor["job"].expected_role or "").strip() == role:
                    continue
                for run_start, run_end in _free_runs(donor["mask"]):
                    run = donor["stream"][run_start:run_end]
                    score, start, end = _best_span(target, run, 0, lookahead=len(run))
                    if score >= RESYNC_THRESHOLD and end > start and (best is None or score > best[0]):
                        best = (score, donor, run_start, run, start, end)
            if best is None:
                continue
            score, donor, run_start, run, start, end = best
            # Полный список текстов роли получателя, а не одна эта строка: две
            # одинаковые перенесённые реплики подряд («Держись ближе ко мне, брат.»
            # дважды) иначе неотличимы от повторного подхода к первой же реплике —
            # `_needed_by_a_later_line` должен увидеть следующую непрочитанную
            # одинаковую строку и не отдать ей место первой, как это делает
            # `align_transcript` для обычной (не заимствованной) сверки.
            recipient_texts = [str(item.get("text") or "") for item in alignment["lines"]]
            takes, _cursor = _collect_takes(target, run, start, end, score, recipient_texts, index)
            if not takes:
                continue
            last_end = run_start + _cursor
            for position in range(run_start + start, last_end):
                donor["mask"][position] = True
            line.update({
                "matched": True, "score": round(score, 3),
                "start": takes[0]["start"], "end": takes[0]["end"], "takes": takes,
                "source_audio_file_id": str(donor["audio"].id),
            })
            changed = True
            report["borrowed"] += 1
            report["roles"][role] = report["roles"].get(role, 0) + 1
            # Одна роль — одна пометка: строка того же номера в другом файле этой роли
            # уже не ненайденная, второй раз её не ищем.
            unheard[role].discard(index)
        if changed:
            touched.add(str(job.id))
    # Заём у соседней роли лёг в один файл роли — остальным её файлам та же строка.
    touched |= _copy_from_siblings(jobs)
    for job, _audio, alignment in jobs:
        if str(job.id) in touched:
            _recount(alignment)
            store_alignment(job, alignment)
    db.flush()
    return report
