"""Прогон консилиума по книге: артефакты чтецов, сведение, арбитр, находки, ход, деньги.

Чистые правила — в `app.pipeline.consilium_run`; здесь всё, что касается базы, диска и сети.
"""
from __future__ import annotations

import logging

import hashlib
import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from app.pipeline.attribution_evidence import find_name_in_narration
from app.pipeline.consilium_prompts import NARRATOR, UNSURE
from app.pipeline.consilium_run import ExistingState
from app.services.routerai_credits import read_credits
from app.time_utils import iso_utc

logger = logging.getLogger(__name__)

#: Слоты чтецов: имена — ключи сохранённых ответов, модели — из «Настройки → Нейросети».
#: Смена модели чтеца не перечитывает уже прочитанные главы: ответ хранится по слоту.
READER_SLOTS = (("opus", "consilium_reader_1"), ("sol", "consilium_reader_2"))


def readers() -> tuple[tuple[str, str, str], ...]:
    """(слот, провайдер, модель) — читается один раз в начале прогона."""
    from app.services.step_models import step_model

    return tuple((slot, *step_model(step)) for slot, step in READER_SLOTS)
ARTIFACT_ROOT = "data/book_reports"
# Факт прогона «Крыльев полумрака» 2026-09-12: чтецы 1 400 ₽ на 13 276 абзацев, арбитр 457 ₽
# на 157 мест. Смета — оценка по этим числам, а не обещание.
READER_RUB_PER_PARAGRAPH = 1400 / 13276
ARBITER_RUB_PER_PLACE = 457 / 157
PLACES_PER_PARAGRAPH = 157 / 13276
READER_CALLS_PER_PARAGRAPH = 2 / 110


@dataclass
class ChapterState:
    index: int
    id: str
    paragraphs: list[tuple[int, str]]
    cast_lines: list[str]
    current: dict[int, str]
    where: dict[int, tuple[str, int, int]]
    # Авторские слова абзаца одной строкой. Имя в них ищется лишь для спорного места
    # (`name_in_narration`): поиск по всей книге занимал минуту сметы.
    narration: dict[int, str]
    speakers: dict[int, str] = field(default_factory=dict)


@dataclass
class BookState:
    book_id: str
    chapters: list[ChapterState]
    canon: Callable[[str], str]
    races: dict[str, str]
    index: dict[str, str] = field(default_factory=dict)  # каст: нормализованный ярлык → имя


def name_in_narration(state: BookState, chapter: ChapterState, ordinal: int) -> str:
    """Имя, названное в авторских словах абзаца, — или `""`."""
    return find_name_in_narration([chapter.narration.get(ordinal, "")], state.index)


class _LazyNarrationNames:
    """Словарь «место → имя в ремарке», который ищет имя только когда его спросили."""

    def __init__(self, state: BookState):
        self._state = state
        self._chapters = {c.index: c for c in state.chapters}
        self._memo: dict[tuple[int, int], str] = {}

    def get(self, key, default=None):
        if key not in self._memo:
            chapter = self._chapters.get(key[0])
            if chapter is None or key[1] not in chapter.narration:
                return default
            self._memo[key] = name_in_narration(self._state, chapter, key[1])
        return self._memo[key]


def load_book_state(db, book_id: str) -> BookState:
    from app.models import Character, ScriptChapter
    from app.pipeline.attribution_resolve import build_cast_index
    from app.pipeline.attribution_triage import normalize_label
    from app.v2.reader import effective_attributions
    from app.v2.store import load_chapter_segments

    cast = db.query(Character).filter(Character.book_id == book_id).all()
    index = build_cast_index([(c.name, [a.strip() for a in (c.aliases or "").split(",") if a.strip()])
                              for c in cast])
    names = {c.name for c in cast}
    races = {c.name: str(c.race or "") for c in cast}

    def canon(raw: str) -> str:
        text = str(raw or "").strip()
        if text in (NARRATOR, ""):
            return text
        if text in names:
            return text
        hit = index.get(normalize_label(text))
        return hit if hit and hit != "<AMBIGUOUS>" else ""

    chapters = []
    for chapter in (db.query(ScriptChapter).filter(ScriptChapter.book_id == book_id)
                    .order_by(ScriptChapter.chapter_index.asc()).all()):
        segments = load_chapter_segments(db, chapter_id=str(chapter.id))
        rows = effective_attributions(db, [s.id for s in segments])
        if not any(rows.get(s.id) for s in segments):
            continue  # глава без разметки — сверять не с чем
        current, where, narration, speakers = {}, {}, {}, {}
        seen: set[str] = set()
        for segment in segments:
            number = int(segment.ordinal)
            spans = rows.get(segment.id, [])
            seen |= {str(s.speaker) for s in spans}
            character = [s for s in spans if s.speaker != NARRATOR]
            longest = max(character, key=lambda s: s.span_end - s.span_start, default=None)
            current[number] = longest.speaker if longest is not None else NARRATOR
            speakers[number] = current[number]
            where[number] = (segment.id,
                             int(longest.span_start) if longest is not None else 0,
                             int(longest.span_end) if longest is not None else len(segment.text or ""))
            narration[number] = " ".join(segment.text[int(s.span_start):int(s.span_end)]
                                         for s in spans if s.speaker == NARRATOR)
        # Каст главы — как в калибровке: персонажи, звучащие в разметке главы.
        cast_lines = [f"- {c.name}" + (f" — {c.race}" if c.race else "")
                      for c in sorted(cast, key=lambda c: c.name)
                      if c.name in seen and c.name != NARRATOR]
        chapters.append(ChapterState(index=int(chapter.chapter_index), id=str(chapter.id),
                                     paragraphs=[(int(s.ordinal), s.text or "") for s in segments],
                                     cast_lines=cast_lines, current=current, where=where,
                                     narration=narration, speakers=speakers))
    return BookState(book_id=book_id, chapters=chapters, canon=canon, races=races, index=index)


def text_fingerprint(paragraphs: list[tuple[int, str]]) -> str:
    digest = hashlib.sha256()
    for number, text in sorted(paragraphs):
        digest.update(f"{number}\x00{len(text)}\x00{text}\x00".encode("utf-8"))
    return digest.hexdigest()


def artifact_path(book_id: str, chapter_index: int, reader: str, root: str = ARTIFACT_ROOT) -> str:
    return os.path.join(root, book_id, "consilium", f"ch{int(chapter_index):02d}-{reader}.json")


def load_answers(book_id: str, chapter: ChapterState, reader: str,
                 root: str = ARTIFACT_ROOT) -> dict[int, str] | None:
    """Ответы чтеца, если они ещё верны: полные и прочитаны по нынешнему тексту главы."""
    path = artifact_path(book_id, chapter.index, reader, root)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    if not data.get("complete") or data.get("text_sha256") != text_fingerprint(chapter.paragraphs):
        return None
    return {int(k): str(v) for k, v in (data.get("answers") or {}).items()}


def save_answers(book_id: str, chapter: ChapterState, reader: str, model: str,
                 answers: dict[int, str], complete: bool, root: str = ARTIFACT_ROOT) -> None:
    path = artifact_path(book_id, chapter.index, reader, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"chapter": chapter.index, "reader": reader, "model": model,
               "text_sha256": text_fingerprint(chapter.paragraphs), "complete": bool(complete),
               "answers": {str(k): v for k, v in sorted(answers.items())}}
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)
    os.replace(tmp, path)


def findings_for(state: BookState, answers: dict[str, dict[tuple[int, int], str]]) -> dict[tuple, dict]:
    from app.pipeline.consilium_compare import compare

    current, where = {}, {}
    for chapter in state.chapters:
        for number in chapter.current:
            current[(chapter.index, number)] = chapter.current[number]
            where[(chapter.index, number)] = chapter.where[number]
    out: dict[tuple, dict] = {}
    named = _LazyNarrationNames(state)
    for f in compare(answers.get("opus", {}), answers.get("sol", {}), current, named):
        segment_id, start, end = where[(f.chapter_index, f.ordinal)]
        out[(f.chapter_index, f.ordinal, start, end)] = {
            "kind": f.kind, "current": f.current, "readers": f.readers, "opus": f.opus, "sol": f.sol,
            "chapter_index": f.chapter_index, "ordinal": f.ordinal,
            "segment_id": segment_id, "span_start": start, "span_end": end,
        }
    return out


def existing_states(db, book_id: str) -> dict[tuple, ExistingState]:
    from app.models import ConsiliumFinding

    return {
        (int(r.chapter_index), int(r.ordinal), int(r.span_start), int(r.span_end)):
            ExistingState(str(r.status), str(r.kind), str(r.current_speaker), str(r.reader_opus),
                          str(r.reader_sol), bool(str(r.arbiter_verdict or "")))
        for r in db.query(ConsiliumFinding).filter(ConsiliumFinding.book_id == book_id).all()
    }


def _round_rub(value: float) -> int:
    return int(math.ceil(max(value, 0.0) / 10.0) * 10)


def estimate(db, book_id: str, root: str = ARTIFACT_ROOT, state: BookState | None = None) -> dict:
    """Смета прогона. `state` — уже загруженное состояние книги, чтобы не читать её дважды."""
    from app.pipeline.consilium_run import plan_places

    if state is None:
        state = load_book_state(db, book_id)
    known: dict[str, dict[tuple[int, int], str]] = {reader: {} for reader, _ in READER_SLOTS}
    unread_paragraphs = 0
    unread_chapters = 0
    for chapter in state.chapters:
        got = {reader: load_answers(book_id, chapter, reader, root) for reader, _ in READER_SLOTS}
        if any(value is None for value in got.values()):
            unread_chapters += 1
            unread_paragraphs += len(chapter.paragraphs)
            continue
        for reader, answers in got.items():
            known[reader].update({(chapter.index, n): who for n, who in answers.items()})
    total_paragraphs = sum(len(c.paragraphs) for c in state.chapters)
    found = findings_for(state, known)
    arbitrate, _gone = plan_places(
        {k: (v["kind"], v["current"], v["opus"], v["sol"]) for k, v in found.items()},
        existing_states(db, book_id))
    recheck_places = len(arbitrate) + math.ceil(unread_paragraphs * PLACES_PER_PARAGRAPH)
    reread_places = math.ceil(total_paragraphs * PLACES_PER_PARAGRAPH)
    return {
        "chapters_total": len(state.chapters),
        "chapters_to_read": {"recheck": unread_chapters, "reread": len(state.chapters)},
        "arbiter_places": {"recheck": recheck_places, "reread": reread_places},
        "estimate_rub": {
            "recheck": _round_rub(unread_paragraphs * READER_RUB_PER_PARAGRAPH
                                  + recheck_places * ARBITER_RUB_PER_PLACE),
            "reread": _round_rub(total_paragraphs * READER_RUB_PER_PARAGRAPH
                                 + reread_places * ARBITER_RUB_PER_PLACE),
        },
        "estimate_calls": {
            "recheck": math.ceil(unread_paragraphs * READER_CALLS_PER_PARAGRAPH) + recheck_places,
            "reread": math.ceil(total_paragraphs * READER_CALLS_PER_PARAGRAPH) + reread_places,
        },
    }


MIN_CREDITS_RUB = 50
# Прогон пишет ход после каждого куска главы и каждого места арбитра — это минуты. Час тишины
# у строки `running` значит, что воркер умер (рестарт, OOM, выкатка), а не что он думает.
STALE_RUN_MINUTES = 60
DEAD_RUN_TEXT = "воркер остановился — прогон можно продолжить"
STOP_TEXT = {
    "stopped_by_user": "остановлен вручную",
    "over_budget": "превышена смета вдвое",
    "no_credits": "кончились деньги на балансе RouterAI",
}


def routed_ask(providers: dict[str, str]):
    """`ask(model, system, user, schema)`, который зовёт каждую модель у её провайдера.

    Сигнатура та же, что у подменных `ask` в тестах: провайдер приходит из шага, а не из
    вызова, — чтецы консилиума могут жить у разных провайдеров."""
    def ask(model: str, system: str, user: str, schema: dict) -> dict:
        from app.pipeline.llm_client import _resolve_provider, call_chat

        provider = providers.get(model, "routerai")
        api_key, base_url, mode = _resolve_provider(provider)
        if not api_key:
            raise RuntimeError(f"нет ключа провайдера {provider}")
        out = call_chat(base_url, api_key, model, system, user, mode=mode, force_json=True, json_schema=schema)
        content = (out or {}).get("content") if isinstance(out, dict) else out
        if isinstance(content, str):
            content = json.loads(content.strip() or "{}")
        return content or {}
    return ask


def default_ask(model: str, system: str, user: str, schema: dict) -> dict:
    return routed_ask({})(model, system, user, schema)


def _meta(run) -> dict:
    try:
        return json.loads(run.meta_json or "{}") or {}
    except ValueError:
        return {}


_checkpoint_lock = threading.Lock()


def checkpoint_run(session_factory, run_id: str, meta: dict) -> bool:
    """Записать ход и узнать, не просили ли остановиться. Флаг остановки не затирается."""
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    from sqlalchemy import update

    # Замок — только от своих потоков. Кнопка «остановить» пишет из другого процесса, поэтому
    # запись условная: meta_json меняется, лишь если он тот же, что прочитан. Иначе флаг,
    # поставленный между чтением и записью, был бы затёрт, и прогон не остановился бы.
    with _checkpoint_lock:
        for _attempt in range(5):
            with session_factory() as db:
                run = db.get(BackgroundRun, run_id)
                if run is None:
                    return False
                seen = run.meta_json
                stop = bool(_meta(run).get("stop_requested"))
                now = utcnow_naive()
                written = db.execute(
                    update(BackgroundRun)
                    .where(BackgroundRun.id == run_id, BackgroundRun.meta_json == seen)
                    .values(meta_json=json.dumps(dict(meta, stop_requested=stop), ensure_ascii=False),
                            heartbeat_at=now, updated_at=now)
                    .execution_options(synchronize_session=False)
                ).rowcount
                db.commit()
                if written:
                    return stop
        with session_factory() as db:  # строку всё время кто-то переписывает — ход подождёт
            run = db.get(BackgroundRun, run_id)
            return bool(run is not None and _meta(run).get("stop_requested"))


def _finish(session_factory, run_id: str, status: str, meta: dict, message: str) -> None:
    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    with session_factory() as db:
        run = db.get(BackgroundRun, run_id)
        if run is None:
            return
        run.status = status
        stop = bool(_meta(run).get("stop_requested"))
        run.meta_json = json.dumps(dict(meta, stop_requested=stop), ensure_ascii=False)
        run.error_message = message[:255]
        run.finished_at = utcnow_naive()
        run.updated_at = utcnow_naive()
        db.commit()


def expire_dead_runs(db, book_id: str, kind: str = "consilium") -> int:
    """Отметить упавшим прогон, который числится идущим, но час не подаёт признаков жизни.

    Иначе мёртвая строка `running` держала бы кнопки запуска закрытыми, «остановить» —
    бессильным, а выкатку — без рестарта воркера навсегда. Очередь не стареет: задача может
    честно ждать за другими. Запись условная — живой прогон, успевший отметиться, не задевается.
    Фиксирует вызывающий (`commit`); оплаченное чтецами лежит на диске и дочитывается кнопкой.

    `kind` — вид прогона (`"consilium"` или `"sound"`): ключ строки хода `f"{kind}:{book_id}"`.
    """
    from datetime import timedelta

    from sqlalchemy import func, update

    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    now = utcnow_naive()
    cutoff = now - timedelta(minutes=STALE_RUN_MINUTES)
    last_sign = func.coalesce(BackgroundRun.heartbeat_at, BackgroundRun.started_at, BackgroundRun.created_at)
    return db.execute(
        update(BackgroundRun)
        .where(BackgroundRun.run_key == f"{kind}:{book_id}", BackgroundRun.status == "running",
               last_sign < cutoff)
        .values(status="failed", error_message=DEAD_RUN_TEXT, finished_at=now, updated_at=now)
        .execution_options(synchronize_session="fetch")
    ).rowcount


def request_stop(db, book_id: str, kind: str = "consilium") -> bool:
    from sqlalchemy import update

    from app.models import BackgroundRun
    from app.time_utils import utcnow_naive

    expire_dead_runs(db, book_id, kind=kind)
    run = (db.query(BackgroundRun)
           .filter(BackgroundRun.run_key == f"{kind}:{book_id}",
                   BackgroundRun.status.in_(("queued", "running")))
           .order_by(BackgroundRun.created_at.desc()).first())
    if run is None:
        return False
    if run.status == "queued":
        # Воркер ещё не взял задачу — ждать нечего, остановка сразу. Условно: если он взял её
        # между чтением и записью, прогону ставится флаг, как идущему.
        now = utcnow_naive()
        meta = dict(_meta(run), phase="stopped", stop_requested=True)
        stopped = db.execute(
            update(BackgroundRun)
            .where(BackgroundRun.id == run.id, BackgroundRun.status == "queued")
            .values(status="stopped", error_message=STOP_TEXT["stopped_by_user"],
                    meta_json=json.dumps(meta, ensure_ascii=False), finished_at=now, updated_at=now)
            .execution_options(synchronize_session="fetch")
        ).rowcount
        if stopped:
            return True
        db.refresh(run)
        if run.status != "running":
            return False
    run.meta_json = json.dumps(dict(_meta(run), stop_requested=True), ensure_ascii=False)
    db.flush()
    return True


def latest_run(db, book_id: str, kind: str = "consilium") -> dict | None:
    from app.models import BackgroundRun

    expire_dead_runs(db, book_id, kind=kind)
    run = (db.query(BackgroundRun).filter(BackgroundRun.run_key == f"{kind}:{book_id}")
           .order_by(BackgroundRun.created_at.desc()).first())
    if run is None:
        return None
    meta = _meta(run)
    iso = lambda value: iso_utc(value) or ""  # noqa: E731
    return {
        "id": run.id, "status": run.status, "phase": meta.get("phase", ""), "mode": meta.get("mode", ""),
        "chapters_total": int(meta.get("chapters_total") or 0), "chapters_done": int(meta.get("chapters_done") or 0),
        "chapters_skipped": int(meta.get("chapters_skipped") or 0),
        "chapters_incomplete": int(meta.get("chapters_incomplete") or 0),
        "arbiter_total": int(meta.get("arbiter_total") or 0), "arbiter_done": int(meta.get("arbiter_done") or 0),
        "spent_rub": float(meta.get("spent_rub") or 0.0), "estimate_rub": float(meta.get("estimate_rub") or 0.0),
        "stop_requested": bool(meta.get("stop_requested")),
        "started_at": iso(run.started_at), "finished_at": iso(run.finished_at),
        "error": str(run.error_message or ""), "result": meta.get("result") or None,
    }


BUSY_STATUSES = ("processing", "char_extracting", "stopping")


def enqueue_consilium(book_id: str, mode: str) -> str | None:
    from app.db import SessionLocal
    from app.models import BackgroundRun
    from app.workers.launcher import enqueue_tracked_task

    return enqueue_tracked_task(
        queue_name="consilium",
        func_ref="app.worker_tasks.perform_consilium_task",
        session_factory=SessionLocal,
        background_run_cls=BackgroundRun,
        job_kind="consilium",
        entity_type="script_book",
        entity_id=book_id,
        run_key=f"consilium:{book_id}",
        meta={"mode": mode, "phase": "queued"},
        book_id=book_id,
        mode=mode,
    )


def _book_title(session_factory, book_id: str) -> str:
    from app.models import ScriptBook

    with session_factory() as db:
        book = db.get(ScriptBook, book_id)
        return str((book.display_title or book.title) if book else book_id)


def run_consilium(*, session_factory, book_id: str, run_id: str, mode: str, ask=None,
                  read_credits=read_credits, notify: Callable[[str], None] | None = None,
                  root: str = ARTIFACT_ROOT, arbiter_workers: int = 6) -> dict:
    from app.pipeline.consilium_run import Budget, StopRun, arbitrate_place, plan_places, read_chapter
    from app.services.consilium_store import apply_run
    from app.services.step_models import MissingKeyError, require_key_for, step_model

    real_calls = ask is None  # подменный ask в тестах не тратит деньги — ключ ему не нужен

    key = "reread" if mode == "reread" else "recheck"
    budget: Budget | None = None
    credits_before: float | None = None
    meta = {"mode": key, "phase": "readers", "chapters_total": 0, "chapters_done": 0,
            "chapters_skipped": 0, "chapters_incomplete": 0, "arbiter_total": 0, "arbiter_done": 0,
            "spent_rub": 0.0, "estimate_rub": 0.0, "credits_before": None}
    result = {"findings": 0, "added": 0, "updated": 0, "gone": 0, "arbitrated": 0,
              "chapters_incomplete": 0, "spent_rub": 0.0, "status": "done", "reason": ""}

    def spent(stop_when_empty: bool = True) -> float | None:
        now = read_credits()
        if now is None or credits_before is None:
            return None
        if stop_when_empty and now < MIN_CREDITS_RUB:
            raise StopRun("no_credits")
        return round(credits_before - now, 2)

    budget_lock = threading.Lock()  # чтецы и арбитр зовут модель из нескольких потоков

    def counted_ask(model, system, user, schema):
        """Предохранитель без баланса считает только настоящие вызовы модели, а не чекпойнты."""
        with budget_lock:
            budget.note_call()
        return ask(model, system, user, schema)

    found: dict[tuple, dict] = {}
    verdicts: dict[tuple, dict] = {}

    def rows_for(places) -> list[dict]:
        rows = []
        for place in places:
            item, verdict = found[place], verdicts.get(place) or {}
            rows.append({
                "chapter_index": item["chapter_index"], "ordinal": item["ordinal"],
                "segment_id": item["segment_id"], "span_start": item["span_start"], "span_end": item["span_end"],
                "kind": item["kind"], "current_speaker": item["current"], "readers_speaker": item["readers"],
                "reader_opus": item["opus"], "reader_sol": item["sol"],
                "arbiter_verdict": str(verdict.get("verdict") or ""),
                "arbiter_speaker": str(verdict.get("speaker") or ""),
                "evidence_para": int(verdict.get("evidence_para") or 0),
                "evidence_quote": str(verdict.get("evidence_quote") or ""),
                "evidence_proven": bool(verdict.get("proven")), "reason": str(verdict.get("reason") or ""),
            })
        return rows

    def checkpoint(check_money: bool = False) -> None:
        with budget_lock:
            if check_money:
                value = spent()
                if value is not None:
                    meta["spent_rub"] = value
                budget.check(value)
        if checkpoint_run(session_factory, run_id, meta):
            raise StopRun("stopped_by_user")

    try:
        # Модели — один раз на прогон: смена на странице действует со следующего прогона.
        slots = readers()
        arbiter_provider, arbiter_model = step_model("consilium_arbiter")
        if real_calls:
            for step in ("consilium_reader_1", "consilium_reader_2", "consilium_arbiter"):
                try:
                    require_key_for(step)
                except MissingKeyError as err:
                    raise StopRun(str(err))
            ask = routed_ask({**{m: p for _s, p, m in slots}, arbiter_model: arbiter_provider})
        # Подготовка — внутри `try`: упавшая смета или база иначе оставили бы строку `running`
        # без причины и без уведомления.
        with session_factory() as db:
            state = load_book_state(db, book_id)
            plan = estimate(db, book_id, root=root, state=state)
        budget = Budget(plan["estimate_rub"][key], plan["estimate_calls"][key])
        credits_before = read_credits()
        meta.update(chapters_total=len(state.chapters), estimate_rub=float(plan["estimate_rub"][key]),
                    credits_before=credits_before)
        if credits_before is not None and credits_before < max(MIN_CREDITS_RUB, plan["estimate_rub"][key]):
            raise StopRun("no_credits")
        answers: dict[str, dict[tuple[int, int], str]] = {reader: {} for reader, _p, _m in slots}
        for chapter in state.chapters:
            got = {} if key == "reread" else {r: load_answers(book_id, chapter, r, root) for r, _p, _m in slots}
            todo = [(r, m) for r, _p, m in slots if got.get(r) is None]
            if not todo:
                meta["chapters_skipped"] += 1
            def read_one(reader_model):
                reader, model = reader_model
                read, complete = read_chapter(model=model, cast_lines=chapter.cast_lines,
                                              paragraphs=chapter.paragraphs, ask=counted_ask,
                                              checkpoint=checkpoint)
                if not complete:
                    # Перечитка с отказом куска не затирает полный ответ по тому же тексту:
                    # недочитанные абзацы берутся из него, и глава остаётся прочитанной.
                    old = load_answers(book_id, chapter, reader, root)
                    if old is not None:
                        read = {**old, **read}
                        complete = {n for n, _ in chapter.paragraphs} <= set(read)
                save_answers(book_id, chapter, reader, model, read, complete, root)
                return reader, read
            with ThreadPoolExecutor(max_workers=max(1, len(todo))) as pool:
                for reader, read in pool.map(read_one, todo):
                    got[reader] = read
            for reader, _p, _m in slots:
                answers[reader].update({(chapter.index, n): who for n, who in (got.get(reader) or {}).items()})
            wanted = {n for n, _ in chapter.paragraphs}
            if any(not wanted <= set(got.get(reader) or {}) for reader, _p, _m in slots):
                meta["chapters_incomplete"] += 1
            meta["chapters_done"] += 1
            checkpoint(check_money=bool(todo))

        meta["phase"] = "compare"
        checkpoint()
        found.update(findings_for(state, answers))
        with session_factory() as db:
            existing = existing_states(db, book_id)
        to_arbitrate, gone = plan_places(
            {k: (v["kind"], v["current"], v["opus"], v["sol"]) for k, v in found.items()}, existing)
        # Снять находку можно, только если оба чтеца ответили по её абзацу в этом прогоне:
        # сведение молчит о неотвеченном абзаце, и отказ куска или сбой сети иначе «снимали» бы
        # настоящие открытые находки — при сбое у всей книги.
        answered = set(answers["opus"]) & set(answers["sol"])
        gone = [k for k in gone if (k[0], k[1]) in answered]
        meta.update(phase="arbiter", arbiter_total=len(to_arbitrate))
        checkpoint()
        chapters = {c.index: c for c in state.chapters}

        halt = threading.Event()  # место, взятое потоком уже после остановки, модель не зовёт

        def judge(place):
            if halt.is_set():
                return place, None
            item = found[place]
            chapter = chapters[item["chapter_index"]]
            verdict = arbitrate_place(chapter_index=chapter.index, ordinal=item["ordinal"],
                                      current=item["current"], opus=item["opus"], sol=item["sol"],
                                      texts=dict(chapter.paragraphs), speakers=chapter.speakers,
                                      races=state.races, ask=counted_ask, canon=state.canon, model=arbiter_model)
            return place, verdict

        # Отмена очереди мест — явно, а не через финализацию генератора `map`: остановка не должна
        # оплачивать остаток книги. Уже взятые потоками места (≤ arbiter_workers) дорабатывают.
        pool = ThreadPoolExecutor(max_workers=max(1, arbiter_workers))
        futures = [pool.submit(judge, place) for place in to_arbitrate]
        try:
            for future in futures:
                place, verdict = future.result()
                verdicts[place] = verdict
                meta["arbiter_done"] += 1
                checkpoint(check_money=meta["arbiter_done"] % 10 == 0)
        finally:
            halt.set()
            pool.shutdown(wait=True, cancel_futures=True)
            for future in futures:  # доработавшие после остановки места тоже оплачены — не терять
                if future.done() and not future.cancelled() and future.exception() is None:
                    place, verdict = future.result()
                    if verdict is not None:
                        verdicts.setdefault(place, verdict)

        meta["phase"] = "saving"
        rows = rows_for(to_arbitrate)
        with session_factory() as db:
            saved = apply_run(db, book_id=book_id, run_label=f"consilium-{run_id[:8]}", rows=rows, gone_keys=gone)
            db.commit()
        final_spent = spent(stop_when_empty=False)  # всё уже записано: пустой баланс — не остановка
        result.update(findings=len(found), arbitrated=len(to_arbitrate), **saved,
                      chapters_incomplete=meta["chapters_incomplete"],
                      spent_rub=final_spent if final_spent is not None else meta["spent_rub"])
        meta.update(phase="done", spent_rub=result["spent_rub"], result=result)
        _finish(session_factory, run_id, "done", meta, "")
    except StopRun as stop:
        message = STOP_TEXT.get(stop.reason, stop.reason)
        if verdicts:  # оплаченные вердикты записываются; «снятых» при остановке не бывает — книга не дочитана
            try:
                with session_factory() as db:
                    saved = apply_run(db, book_id=book_id, run_label=f"consilium-{run_id[:8]}",
                                      rows=rows_for(sorted(verdicts)), gone_keys=[])
                    db.commit()
                result.update(added=saved["added"], updated=saved["updated"], arbitrated=len(verdicts))
            except Exception as exc:  # noqa: BLE001 — остановка всё равно фиксируется
                message = f"{message}; вердикты не записаны: {type(exc).__name__}: {exc}"
        now_spent = spent(stop_when_empty=False)  # остановка — не повод показывать старую трату
        if now_spent is not None:
            meta["spent_rub"] = now_spent
        result.update(status="stopped", reason=stop.reason, spent_rub=meta["spent_rub"], findings=len(found),
                      chapters_incomplete=meta["chapters_incomplete"])
        meta.update(phase="stopped", result=result)
        _finish(session_factory, run_id, "stopped", meta, message)
    except Exception as exc:  # noqa: BLE001 — сбой фиксируется в ходе и уходит в уведомление
        result.update(status="failed", reason=f"{type(exc).__name__}: {str(exc)[:200]}")
        meta.update(phase="failed", result=result)
        _finish(session_factory, run_id, "failed", meta, result["reason"])
        _notify_owner(session_factory, notify, book_id, result)
        raise
    _notify_owner(session_factory, notify, book_id, result)
    return result


def _notify_owner(session_factory, notify, book_id: str, result: dict) -> None:
    try:
        title = _book_title(session_factory, book_id)
    except Exception:  # noqa: BLE001 — сбой базы не должен подменять собой исходную ошибку прогона
        title = book_id
    if result["status"] == "done":
        incomplete = (f"; неполных глав: {result['chapters_incomplete']} — пересверка их дочитает"
                      if result.get("chapters_incomplete") else "")
        text = (f"Консилиум «{title}»: находок {result['findings']}, новых {result['added']}, "
                f"обновлено {result['updated']}, снято {result['gone']}{incomplete}; "
                f"потрачено ≈ {result['spent_rub']:.0f} ₽.")
    elif result["status"] == "stopped":
        text = f"Консилиум «{title}» остановлен: {STOP_TEXT.get(result['reason'], result['reason'])}; потрачено ≈ {result['spent_rub']:.0f} ₽."
    else:
        text = f"Консилиум «{title}» упал: {result['reason']}"
    try:
        if notify is not None:
            notify(text)
        else:
            from app.services.telegram import send_telegram_message
            with session_factory() as db:
                send_telegram_message(db, text)
    except Exception:  # noqa: BLE001 — уведомление не валит прогон
        logger.exception("Итог прогона книги %s не отправлен в Telegram", book_id)
