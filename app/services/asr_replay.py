"""Пересчёт сверки из сохранённого услышанного — без обращения к распознаванию.

Зачем отдельный модуль, а не ещё один путь внутри `asr_run`: у прогона распознавания и
у пересчёта разные обязанности и разные последствия. Прогон тратит деньги, зовёт сеть и
сообщает диктору о пропусках, пока тот ещё у микрофона. Пересчёт не делает ничего из
этого: он берёт уже услышанное и применяет к нему текущие правила сверки.

Уведомлений здесь нет намеренно. Пересчёт — не событие записи: диктор неделю назад
прислал дубль и получил своё письмо тогда же, и второе письмо о тех же пропусках — от
того, что МЫ поменяли правила, — сказало бы ему неправду о его работе.
"""
import json

from app.services.asr_align import align_transcript
from app.services.asr_run import expected_lines, store_alignment


def _shape(alignment: dict) -> dict:
    """Три числа, по которым видно, что пересчёт сделал с главой."""
    lines = alignment.get("lines") or []
    return {
        "coverage": float(alignment.get("coverage") or 0.0),
        "missing": len(alignment.get("missing") or []),
        "multi_take": sum(1 for line in lines if len(line.get("takes") or []) > 1),
    }


def realign_job(db, job_id: str, *, write: bool = True) -> dict | None:
    """Пересчитать сверку одной джобы. `None` — переигрывать нечем.

    Нечем — это джоба, посчитанная до появления графы `heard_json`: её вход выброшен.
    Такая строка не ошибка и не повод останавливать прогон, поэтому `None`, а не
    исключение: на ней просто нечего делать.

    `write=False` — холостой прогон: считает и отчитывается, в базу не пишет. Нужен
    затем, чтобы последствия правки сверки можно было увидеть до того, как она
    применена к архиву.
    """
    from app.models import AsrJob

    job = db.get(AsrJob, str(job_id or "").strip())
    if job is None or not str(job.heard_json or "").strip():
        return None

    heard = json.loads(job.heard_json)
    expected = expected_lines(db, str(job.chapter_id or ""), str(job.expected_role or ""))
    alignment = align_transcript(expected, heard.get("segments") or [])

    before = json.loads(job.alignment_json) if str(job.alignment_json or "").strip() else {}
    report = {
        "job_id": str(job.id),
        "role": str(job.expected_role or ""),
        "chapter_index": int(job.expected_chapter_index or 0),
        "before": _shape(before),
        "after": _shape(alignment),
    }
    # «Изменилось» решается по итогу сверки, а не по числам: одно и то же покрытие
    # бывает у разной раскладки реплик, и такая правка тоже меняет сессию монтажёра.
    after_json = json.dumps(alignment, ensure_ascii=False)
    report["changed"] = after_json != str(job.alignment_json or "")
    if write:
        store_alignment(job, alignment)
        db.flush()
    return report


def realign_chapter(db, chapter_id: str, *, notify: bool = True) -> dict:
    """Пересчитать сверку всей главы после правки разметки — и сказать актёру о новом.

    `notify=False` — писем не отправлять, а вернуть их в `pending_letters`: вызывающий,
    который коммитит сам (фон, скрипт), шлёт их `send_moved_letters` ПОСЛЕ коммита.

    Порядок важен: сначала чистая сверка всех файлов (она стирает прежние заимствования),
    затем поиск у соседних ролей, и только потом сравнение «до/после». Письмо — только о
    строках, которых до правки в пропусках роли не было: старые пропуски диктор получил при
    приёме дубля, и повтор здесь сказал бы ему неправду (см. шапку модуля).
    """
    from app.models import AsrJob, ScriptBook, ScriptChapter
    from app.services.asr_borrow import borrow_across_roles, chapter_done_jobs, role_unheard_texts

    chapter_id = str(chapter_id or "").strip()
    before_unheard = role_unheard_texts(db, chapter_id)
    before_json = {str(job.id): str(job.alignment_json or "") for job, _a, _al in chapter_done_jobs(db, chapter_id)}

    jobs = db.query(AsrJob).filter(AsrJob.chapter_id == chapter_id, AsrJob.status == "done").all()
    # `None` — джоба до появления `heard_json`: пересчитывать нечем (см. `realign_job`).
    skipped = sum(1 for job in jobs if realign_job(db, str(job.id)) is None)
    borrowed = borrow_across_roles(db, chapter_id)["borrowed"]

    after_jobs = chapter_done_jobs(db, chapter_id)
    changed = any(before_json.get(str(job.id), "") != str(job.alignment_json or "") for job, _a, _al in after_jobs)
    new_missing: dict[str, list[str]] = {}
    for role, texts in role_unheard_texts(db, chapter_id).items():
        old = set(before_unheard.get(role, []))
        fresh = [text for text in texts if text not in old]
        if fresh:
            new_missing[role] = fresh

    pending_letters = []
    if new_missing:
        chapter = db.get(ScriptChapter, chapter_id)
        book = db.get(ScriptBook, chapter.book_id) if chapter is not None else None
        reader_of = {}
        for job, audio, _alignment in after_jobs:  # по загрузке: последний загруженный побеждает
            reader_of[str(job.expected_role or "").strip()] = str(audio.actor_name or "")
        for role, texts in new_missing.items():
            pending_letters.append({
                "actor_name": reader_of.get(role, ""), "role": role,
                "chapter": str(getattr(chapter, "chapter_title", "") or ""),
                "book_title": str(getattr(book, "title", "") or ""),
                "book_id": str(getattr(chapter, "book_id", "") or ""), "texts": texts,
            })
    notified = send_moved_letters(db, pending_letters) if notify else []
    if changed:
        from app.services.chapter_delivery import mark_session_outdated

        mark_session_outdated(db, chapter_id)
    db.flush()
    return {"chapter_id": chapter_id, "jobs": len(jobs), "changed": changed, "borrowed": borrowed,
            "skipped": skipped, "new_missing": new_missing, "notified": notified,
            "pending_letters": [] if notify else pending_letters}


def send_moved_letters(db, pending_letters) -> list[dict]:
    """Отправить письма о перенесённых репликах, собранные `realign_chapter(notify=False)`.

    Отдельно затем, чтобы фон и скрипт сначала закоммитили пересчёт и только потом писали:
    письмо, ушедшее до упавшего коммита («database is locked»), ушло бы снова на
    следующей же правке — сверка в базе осталась бы старой, и строка снова «новая».
    """
    from app.services import asr_notice

    return [asr_notice.notify_moved_lines(db, **letter) for letter in pending_letters or []]


def enqueue_realign_for_chapter(chapter_id: str) -> str | None:
    """Поставить пересчёт главы в очередь `high` — ту же, что у распознавания.

    Без дедупликации: у очереди один воркер, задания идут по одному, пересчёт идемпотентен
    и пишет только о новых пропусках. Дедупликация по ключу потеряла бы правку, сделанную,
    пока прогон уже читает разметку.
    """
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    chapter_id = str(chapter_id or "").strip()
    if not chapter_id:
        return None
    return enqueue_tracked_task(
        queue_name="high",
        func_ref="app.worker_tasks.perform_realign_chapter_task",
        session_factory=SessionLocal,
        background_run_cls=BackgroundRun,
        job_kind="asr_realign_chapter",
        entity_type="script_chapter",
        entity_id=chapter_id,
        run_key=f"realign:{chapter_id}",
        meta={"chapter_id": chapter_id},
        dedupe=False,
        chapter_id=chapter_id,
    )
