"""Звуковая разметка книги: главы по порядку, растущий список мест, деньги, остановка.

Движок — по образцу консилиума (`consilium_engine`): тот же клиент RouterAI, та же строка
хода `BackgroundRun`, та же очередь и служба. Правила разбора — в `app.pipeline.sound_markers`,
база — в `app.services.sound_store`.
"""
from __future__ import annotations

import logging

import json
import math
import os
from typing import Callable

from app.services.consilium_engine import (
    ARTIFACT_ROOT, MIN_CREDITS_RUB, STOP_TEXT, _finish, checkpoint_run, text_fingerprint,
)
from app.services.routerai_credits import read_credits

logger = logging.getLogger(__name__)

KIND = "sound"
#: модель по умолчанию; настоящая берётся из шага «sound» в начале прогона
MODEL = "anthropic/claude-opus-5"
# До пилота — по консилиуму: чтецы Opus+Sol стоили 1 400 ₽ на 13 276 абзацев «Крыльев».
# Звуковая разметка — один Opus, но с длинным ответом; пилот на одной главе уточняет число.
RUB_PER_PARAGRAPH = 0.1
ATTEMPTS = 2


def book_chapters(db, book_id: str, chapter_id: str = "") -> list[tuple[str, int, list[tuple[int, str]]]]:
    from app.models import ScriptChapter
    from app.v2.store import load_chapter_segments

    query = db.query(ScriptChapter).filter(ScriptChapter.book_id == book_id)
    if chapter_id:
        query = query.filter(ScriptChapter.id == chapter_id)
    out = []
    for chapter in query.order_by(ScriptChapter.chapter_index.asc()).all():
        paragraphs = [(int(s.ordinal), s.text or "") for s in load_chapter_segments(db, chapter_id=str(chapter.id))]
        if paragraphs:
            out.append((str(chapter.id), int(chapter.chapter_index), paragraphs))
    return out


def sidecar_path(book_id: str, index: int, root: str = ARTIFACT_ROOT) -> str:
    return os.path.join(root, book_id, "sound", f"ch{int(index):02d}.json")


def load_sidecar(book_id: str, index: int, paragraphs, root: str = ARTIFACT_ROOT) -> dict | None:
    try:
        with open(sidecar_path(book_id, index, root), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not data.get("complete") or data.get("text_sha256") != text_fingerprint(paragraphs):
        return None
    return data


def save_sidecar(book_id: str, index: int, paragraphs, data: dict, root: str = ARTIFACT_ROOT,
                 model: str = MODEL) -> None:
    path = sidecar_path(book_id, index, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = dict(data, chapter=index, model=model, text_sha256=text_fingerprint(paragraphs))
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(tmp, path)


def _rub(value: float) -> int:
    return int(math.ceil(max(value, 0.0) / 10.0) * 10)


def estimate(db, book_id: str, root: str = ARTIFACT_ROOT, chapter_id: str = "") -> dict:
    chapters = book_chapters(db, book_id, chapter_id)
    unread = [c for c in chapters if load_sidecar(book_id, c[1], c[2], root) is None]
    count = lambda items: sum(len(c[2]) for c in items)
    return {
        "chapters_total": len(chapters),
        "chapters_to_read": {"rest": len(unread), "all": len(chapters)},
        "estimate_rub": {"rest": _rub(count(unread) * RUB_PER_PARAGRAPH),
                         "all": _rub(count(chapters) * RUB_PER_PARAGRAPH)},
        "estimate_calls": {"rest": len(unread) * ATTEMPTS + 1, "all": len(chapters) * ATTEMPTS + 1},
    }


def enqueue_sound(book_id: str, mode: str, chapter_id: str = "") -> str | None:
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    return enqueue_tracked_task(
        queue_name="consilium",
        func_ref="app.worker_tasks.perform_sound_task",
        session_factory=SessionLocal,
        background_run_cls=BackgroundRun,
        job_kind=KIND,
        entity_type="script_book",
        entity_id=book_id,
        run_key=f"{KIND}:{book_id}",
        meta={"mode": mode, "phase": "queued", "chapter_id": chapter_id},
        book_id=book_id,
        mode=mode,
        chapter_id=chapter_id,
    )


def run_sound(*, session_factory, book_id: str, run_id: str, mode: str, chapter_id: str = "",
              ask=None, read_credits=read_credits, notify: Callable[[str], None] | None = None,
              root: str = ARTIFACT_ROOT) -> dict:
    from app.pipeline.consilium_run import Budget, StopRun
    from app.pipeline.sound_markers import MERGE_SCHEMA, MERGE_SYSTEM, SCHEMA, SYSTEM, chapter_prompt, merge_prompt, validate
    from app.services.sound_store import apply_chapter, place_rows, record_pairs, touch_sessions

    key = "rest" if mode == "rest" else "all"
    meta = {"mode": mode, "phase": "chapters", "chapters_total": 0, "chapters_done": 0, "chapters_skipped": 0,
            "chapters_incomplete": 0, "spent_rub": 0.0, "estimate_rub": 0.0}
    result = {"status": "done", "reason": "", "chapters_done": 0, "chapters_skipped": 0,
              "chapters_incomplete": 0, "markers": 0, "places_new": 0, "pairs": 0, "spent_rub": 0.0,
              "merge_error": ""}
    credits_before = None
    budget = None

    def checkpoint(check_money: bool = False) -> None:
        if check_money:
            now = read_credits()
            if now is not None and credits_before is not None:
                if now < MIN_CREDITS_RUB:
                    raise StopRun("no_credits")
                meta["spent_rub"] = round(credits_before - now, 2)
                budget.check(meta["spent_rub"])
            else:
                budget.check(None)
        if checkpoint_run(session_factory, run_id, meta):
            raise StopRun("stopped_by_user")

    from app.services.consilium_engine import routed_ask
    from app.services.step_models import MissingKeyError, require_key_for, step_model

    provider, model = step_model("sound")  # один раз на прогон
    real_calls = ask is None  # подменный ask в тестах не тратит деньги — ключ ему не нужен
    if real_calls:
        ask = routed_ask({model: provider})
        if provider != "routerai":
            # баланс RouterAI к звуку на другом провайдере не относится — ограничение по вызовам
            read_credits = lambda: None  # noqa: E731

    def counted(system, user, schema):
        budget.note_call()
        return ask(model, system, user, schema)

    try:
        if real_calls:
            try:
                require_key_for("sound")
            except MissingKeyError as err:
                raise StopRun(str(err))
        with session_factory() as db:
            chapters = book_chapters(db, book_id, chapter_id if mode == "chapter" else "")
            plan = estimate(db, book_id, root=root, chapter_id=chapter_id if mode == "chapter" else "")
        budget = Budget(plan["estimate_rub"][key], plan["estimate_calls"][key])
        credits_before = read_credits()
        meta.update(chapters_total=len(chapters), estimate_rub=float(plan["estimate_rub"][key]))
        if credits_before is not None and credits_before < max(MIN_CREDITS_RUB, plan["estimate_rub"][key]):
            raise StopRun("no_credits")
        for cid, index, paragraphs in chapters:
            if mode == "rest" and load_sidecar(book_id, index, paragraphs, root) is not None:
                meta["chapters_skipped"] += 1
                meta["chapters_done"] += 1
                checkpoint()
                continue
            with session_factory() as db:
                places = place_rows(db, book_id)
            answer, error = None, ""
            for _attempt in range(ATTEMPTS):
                try:
                    answer = counted(SYSTEM, chapter_prompt(paragraphs, places), SCHEMA)
                    break
                except StopRun:
                    raise
                except Exception as exc:  # noqa: BLE001 — глава останется неполной, прогон идёт дальше
                    error = f"{type(exc).__name__}: {str(exc)[:200]}"
            if answer is None:
                save_sidecar(book_id, index, paragraphs, {"complete": False, "error": error}, root, model)
                meta["chapters_incomplete"] += 1
            else:
                # Модель может вернуть кривой ответ (например, "start": null) — validate()
                # или запись в базу тогда упадёт исключением. Это не сбой всего прогона:
                # неполной остаётся только эта глава, книга дочитывается дальше.
                try:
                    checked = validate(paragraphs, answer, {p["id"] for p in places})
                    with session_factory() as db:
                        applied = apply_chapter(db, book_id=book_id, chapter_id=cid,
                                                text_sha256=text_fingerprint(paragraphs), checked=checked,
                                                run_id=run_id, texts=dict(paragraphs))
                        touch_sessions(db, [cid])
                        db.commit()
                    result["markers"] += applied["added"]
                    result["places_new"] += applied["places_new"]
                    save_sidecar(book_id, index, paragraphs, {"complete": True, "answer": answer,
                                                              "dropped": checked.dropped}, root, model)
                except StopRun:
                    raise
                except Exception as exc:  # noqa: BLE001 — кривой ответ модели не валит весь прогон
                    error = f"{type(exc).__name__}: {str(exc)[:200]}"
                    save_sidecar(book_id, index, paragraphs, {"complete": False, "error": error}, root, model)
                    meta["chapters_incomplete"] += 1
            meta["chapters_done"] += 1
            checkpoint(check_money=True)

        if mode != "chapter":
            meta["phase"] = "places"
            checkpoint()
            with session_factory() as db:
                places = place_rows(db, book_id)
            # Ничего не дочитали (весь `rest` уже был прочитан раньше) — новых мест и пар
            # взяться неоткуда, платный вызов на склейку не нужен.
            read_this_run = meta["chapters_done"] - meta["chapters_skipped"]
            if len(places) > 1 and read_this_run > 0:
                try:
                    answer = counted(MERGE_SYSTEM, merge_prompt(places), MERGE_SCHEMA)
                    with session_factory() as db:
                        result["pairs"] = record_pairs(db, book_id, (answer or {}).get("pairs") or [])
                        db.commit()
                except StopRun:
                    raise
                except Exception as exc:  # noqa: BLE001 — сорвавшаяся склейка не должна топить готовый прогон глав
                    result["merge_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        result.update(chapters_done=meta["chapters_done"], chapters_skipped=meta["chapters_skipped"],
                      chapters_incomplete=meta["chapters_incomplete"], spent_rub=meta["spent_rub"])
        meta.update(phase="done", result=result)
        _finish(session_factory, run_id, "done", meta, "")
    except StopRun as stop:
        result.update(status="stopped", reason=stop.reason, chapters_done=meta["chapters_done"],
                      chapters_incomplete=meta["chapters_incomplete"], spent_rub=meta["spent_rub"])
        meta.update(phase="stopped", result=result)
        _finish(session_factory, run_id, "stopped", meta, STOP_TEXT.get(stop.reason, stop.reason))
    except Exception as exc:  # noqa: BLE001
        result.update(status="failed", reason=f"{type(exc).__name__}: {str(exc)[:200]}")
        meta.update(phase="failed", result=result)
        _finish(session_factory, run_id, "failed", meta, result["reason"])
        _notify(session_factory, notify, book_id, result)
        raise
    _notify(session_factory, notify, book_id, result)
    return result


def _notify(session_factory, notify, book_id: str, result: dict) -> None:
    from app.services.consilium_engine import _book_title
    try:
        title = _book_title(session_factory, book_id)
    except Exception:  # noqa: BLE001
        title = book_id
    if result["status"] == "done":
        text = (f"Звуковая разметка «{title}»: глав {result['chapters_done']}, "
                f"новых маркеров {result['markers']}, "
                f"новых мест {result['places_new']}, пар мест на решение {result['pairs']}"
                + (f", неполных глав {result['chapters_incomplete']}" if result["chapters_incomplete"] else "")
                + (f"; склейка мест не прошла: {result['merge_error']}" if result.get("merge_error") else "")
                + f"; потрачено ≈ {result['spent_rub']:.0f} ₽.")
    elif result["status"] == "stopped":
        text = f"Звуковая разметка «{title}» остановлена: {STOP_TEXT.get(result['reason'], result['reason'])}."
    else:
        text = f"Звуковая разметка «{title}» упала: {result['reason']}"
    try:
        if notify is not None:
            notify(text)
        else:
            from app.services.telegram import send_telegram_message
            with session_factory() as db:
                send_telegram_message(db, text)
    except Exception:  # noqa: BLE001 — уведомление не валит прогон
        logger.exception("Итог прогона книги %s не отправлен в Telegram", book_id)
