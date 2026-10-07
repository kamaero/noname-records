"""The v2 book pipeline: segment, cast, attribute, stress — and the book is ready to review.

Four steps, each a plain function over `(db, book, run, ctx)` that walks the chapters
and returns a summary. The walk is shared: one chapter at a time, a heartbeat on the
run row after each, a look at `stop_requested` before the next. That is what makes
the pipeline stoppable and resumable without any bookkeeping of its own — a chapter
that already has what a step produces is skipped on the next run unless `force`.

The steps reuse what the three scripts (`segment_book`, `attribute_book`,
`stress_book`) already do; the chapter-level pieces that used to live only in the
scripts moved here so the scripts and the pipeline run the same code. The cast step
is the one place v2 calls into the old pipeline: `_run_char_extraction` is the
book-level cast extraction v2 keeps as its first model call, and it is called only
when the book has no characters yet.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from app.config import settings
from app.services import spend
from app.models import Character, LlmUsageLog, ScriptBook, ScriptChapter, ScriptJob, ScriptLog
from app.time_utils import utcnow_naive
from app.v2.attribute import NARRATOR
from app.v2.models import V2Attribution, V2Run, V2Segment, V2StressMark
from app.v2.run import attribute_chapter, merge_stats, new_stats
from app.v2 import model_catalog
from app.v2.segmenter import segment_chapter
from app.v2.store import load_chapter_segments, store_attributions, store_chapter_segments, store_stress_marks
from app.v2.stress import Resolver, load_author_layer, stress_segment
from app.v2.units import units_from_segments

logger = logging.getLogger(__name__)

STEPS = ("segment", "cast", "attribute", "stress")
STEP_LABELS = {
    "segment": "Сегментация",
    "cast": "Персонажи",
    "attribute": "Роли",
    "stress": "Ударения",
}
HEADING_SOURCE = "rule"
LLM_SOURCES = ("llm", "llm_review", HEADING_SOURCE)
USAGE_STAGE = "v2_attribution"
FINAL_STATUS = "author_review"
V2_MODE = "v2"


def is_v2_book(book) -> bool:
    """True when the book is the v2 pipeline's and the old orchestrator must leave it alone."""
    return str(getattr(book, "pipeline_mode", "") or "").strip().lower() == V2_MODE


class StopRequested(Exception):
    """Raised between chapters when the book's `stop_requested` flag is set."""


@dataclass
class RunContext:
    """What a step needs besides the database: options, the model, and the callbacks."""

    force: bool = False
    provider: str = ""
    model: str = ""
    thinking: str = "off"
    review: bool = True
    llm: Callable | None = None
    resolver: Resolver | None = None
    # "auto" loads RUAccent when it is installed; None skips the context layer.
    context: object = "auto"
    on_progress: Callable[[str], None] | None = None
    on_heartbeat: Callable[[], None] | None = None
    started: float = field(default_factory=time.monotonic)

    def say(self, message: str) -> None:
        if self.on_progress:
            self.on_progress(message)

    def get_llm(self):
        if self.llm is None:
            from app.v2.llm import make_llm
            from app.v2.model_catalog import CATALOG

            # A catalogued model carries its own knob: DeepSeek is told not to think,
            # Muse Spark is told to think as little as its endpoint allows.
            row = next((item for item in CATALOG if item.provider == self.provider and item.model == self.model), None)
            if row is not None:
                extra_body = dict(row.extra_body) if (row.extra_body and self.thinking == "off") else None
            else:
                extra_body = {"thinking": {"type": "disabled"}} if self.thinking == "off" else None
            self.llm = make_llm(self.provider, self.model, extra_body=extra_body)
        return self.llm

    def get_resolver(self, db, book) -> Resolver:
        if self.resolver is None:
            context = self.context
            if context == "auto":
                context = _ruaccent_layer()
            self.resolver = Resolver.default(author=load_author_layer(db, book), context=context)
        return self.resolver


def _ruaccent_layer():
    try:
        from app.v2.stress_ruaccent import context_layer

        return context_layer()
    except Exception as exc:  # noqa: BLE001 — an optional library must not stop the book
        logger.warning("RUAccent unavailable, context layer skipped: %s", exc)
        return None


# --- pieces the scripts used to own -----------------------------------------------------


def _split_aliases(raw: str) -> list[str]:
    return [part.strip() for part in (raw or "").replace(";", ",").split(",") if part.strip()]


def cast_for_chapter(characters, chapter_index: int) -> tuple[dict[str, str], list[str]]:
    """`(cast, cast_lines)` — every name and alias to its canonical name, and the prompt lines.

    A character whose `appears_in` is filled is offered only in those chapters; one
    with it empty is offered everywhere, because an empty field is «unknown», not «nowhere».
    """
    cast: dict[str, str] = {}
    lines: list[str] = []
    for character in characters:
        name = (character.name or "").strip()
        # The old pipeline stored its own UNSURE placeholders as characters
        # («UNSURE: одна из девушек»); offering them as cast would teach the model
        # to answer with a label that means "nobody knows".
        if not name or name == NARRATOR or name.upper().startswith("UNSURE"):
            continue
        appears = {x.strip() for x in (character.appears_in or "").split(",") if x.strip()}
        if appears and str(chapter_index) not in appears:
            continue
        aliases = [a for a in _split_aliases(character.aliases) if a != name]
        cast.setdefault(name, name)
        for alias in aliases:
            cast.setdefault(alias, name)
        lines.append(f"- {name} — {', '.join(aliases)}" if aliases else f"- {name}")
    lines.append(f"- {NARRATOR} — авторская речь, служебный голос")
    return cast, lines


def heading_records(units) -> list[dict]:
    """A chapter title is the narrator's, always — no call is spent asking."""
    return [
        {**unit.as_record(span_start=0, span_end=len(unit.text), speaker=NARRATOR, confidence=1.0),
         "source": HEADING_SOURCE}
        for unit in units
    ]


def attribute_chapter_rows(db, book, chapter, characters, *, llm, provider: str, model: str,
                           review: bool = True, on_batch=None) -> dict:
    """Attribute one chapter's segments and store the answer as the next version.

    `{stored, stats, problems, segments}`; a chapter without segments stores nothing.
    The model's token use is logged under `stage='v2_attribution'` so the budget
    screens see v2 the way they see the old stages.
    """
    segments = load_chapter_segments(db, chapter_id=chapter.id)
    if not segments:
        return {"stored": 0, "stats": new_stats(), "problems": ["нет v2_segments"], "segments": 0}
    units = units_from_segments(segments)
    heading_ids = {s.id for s in segments if s.kind == "heading"}
    cast, cast_lines = cast_for_chapter(characters, int(chapter.chapter_index))
    records, problems, stats = attribute_chapter(
        [u for u in units if u.id not in heading_ids],
        cast=cast, cast_lines=cast_lines, llm=llm, review=review, on_batch=on_batch,
    )
    stored = store_attributions(db, heading_records([u for u in units if u.id in heading_ids]) + records)
    db.add(LlmUsageLog(
        book_id=book.id,
        chapter_id=chapter.id,
        chapter_index=int(chapter.chapter_index),
        stage=USAGE_STAGE,
        provider=provider,
        model=model,
        created_by_user_id=book.created_by_user_id or "",
        created_by_name=book.created_by_name or "",
        prompt_tokens=int(stats["prompt_tokens"]),
        completion_tokens=int(stats["completion_tokens"]),
        total_tokens=int(stats["prompt_tokens"]) + int(stats["completion_tokens"]),
    ))
    return {"stored": stored, "stats": stats, "problems": problems, "segments": len(segments)}


def stress_chapter_rows(db, chapter, resolver: Resolver) -> dict:
    """Stress every segment of one chapter through the resolver chain; `{marks, counts, unresolved}`."""
    segments = load_chapter_segments(db, chapter_id=chapter.id)
    marks_total = 0
    counts: dict[str, int] = {}
    unresolved: dict[str, int] = {}
    for segment in segments:
        marks, report = stress_segment(segment.text, resolver=resolver)
        marks_total += store_stress_marks(db, segment_id=segment.id, marks=marks)
        for source, count in (report.get("counts") or {}).items():
            counts[source] = counts.get(source, 0) + int(count)
        for word in report.get("unresolved") or []:
            unresolved[word] = unresolved.get(word, 0) + 1
    return {"marks": marks_total, "counts": counts, "unresolved": unresolved, "segments": len(segments)}


# --- what a step already did, so a rerun can skip it -------------------------------------


def _chapter_ids_with(db, model, chapter_ids: list[str], *, sources: tuple[str, ...] | None = None) -> set[str]:
    found: set[str] = set()
    ids = [str(item) for item in chapter_ids]
    for i in range(0, len(ids), 500):
        query = (
            db.query(V2Segment.chapter_id)
            .join(model, model.segment_id == V2Segment.id)
            .filter(V2Segment.chapter_id.in_(ids[i : i + 500]))
        )
        if sources:
            query = query.filter(model.source.in_(list(sources)))
        found.update(str(row[0]) for row in query.distinct().all())
    return found


def chapters_with_segments(db, chapter_ids: list[str]) -> set[str]:
    found: set[str] = set()
    ids = [str(item) for item in chapter_ids]
    for i in range(0, len(ids), 500):
        rows = db.query(V2Segment.chapter_id).filter(V2Segment.chapter_id.in_(ids[i : i + 500])).distinct().all()
        found.update(str(row[0]) for row in rows)
    return found


def chapters_attributed(db, chapter_ids: list[str]) -> set[str]:
    """Chapters that hold a model's attribution (not just an import) — done for the attribute step."""
    return _chapter_ids_with(db, V2Attribution, chapter_ids, sources=LLM_SOURCES)


def chapters_stressed(db, chapter_ids: list[str]) -> set[str]:
    return _chapter_ids_with(db, V2StressMark, chapter_ids)


# --- the chapter walk --------------------------------------------------------------------


def _chapters(db, book_id: str) -> list[ScriptChapter]:
    return (
        db.query(ScriptChapter)
        .filter(ScriptChapter.book_id == book_id)
        .order_by(ScriptChapter.chapter_index.asc())
        .all()
    )


def _stop_requested(db, book) -> bool:
    # Read the column fresh: the flag is set from another process while this one runs.
    value = db.query(ScriptBook.stop_requested).filter(ScriptBook.id == book.id).scalar()
    return str(value or "").strip().lower() == "true"


def _touch(db, run: V2Run, ctx: RunContext | None = None, **fields) -> None:
    for name, value in fields.items():
        setattr(run, name, value)
    run.updated_at = utcnow_naive()
    db.commit()
    if ctx is not None and ctx.on_heartbeat:
        try:
            ctx.on_heartbeat()
        except Exception:  # noqa: BLE001 — a dashboard heartbeat must not stop the book
            logger.warning("v2 heartbeat failed", exc_info=True)


def _log(db, book, message: str, level: str = "info") -> None:
    db.add(ScriptLog(book_id=book.id, level=level, message=f"V2: {message}"))


def walk_chapters(db, book, run: V2Run, ctx: RunContext, chapters, work, *, skip: set[str] = frozenset()) -> dict:
    """Run `work(chapter)` over `chapters` one at a time, with a heartbeat after each.

    Before every chapter the stop flag is read; when it is set the walk raises
    `StopRequested` and the run ends `stopped` — never in the middle of a chapter,
    so what was written is whole. Chapters in `skip` count as done without work.
    Returns `{done, skipped, results}`.
    """
    _touch(db, run, ctx, chapters_total=len(chapters), chapters_done=0)
    done = skipped = 0
    results: dict[str, dict] = {}
    for chapter in chapters:
        if _stop_requested(db, book):
            raise StopRequested(f"остановлено перед главой {chapter.chapter_index}")
        if chapter.id in skip:
            skipped += 1
            _touch(db, run, ctx, chapters_done=done + skipped)
            continue
        started = time.monotonic()
        result = work(chapter) or {}
        results[chapter.id] = result
        done += 1
        stats = result.get("stats") or {}
        _touch(
            db, run, ctx,
            chapters_done=done + skipped,
            calls=int(run.calls or 0) + int(stats.get("calls") or 0),
            prompt_tokens=int(run.prompt_tokens or 0) + int(stats.get("prompt_tokens") or 0),
            completion_tokens=int(run.completion_tokens or 0) + int(stats.get("completion_tokens") or 0),
        )
        ctx.say(f"{STEP_LABELS.get(run.step, run.step)}: глава {chapter.chapter_index} "
                f"({done + skipped}/{len(chapters)}) {time.monotonic() - started:.0f}с {_brief(result)}")
    return {"done": done, "skipped": skipped, "results": results}


def _brief(result: dict) -> str:
    parts = []
    for key in ("segments", "stored", "marks"):
        if key in result:
            parts.append(f"{key}={result[key]}")
    stats = result.get("stats") or {}
    if stats:
        parts.append(f"calls={stats.get('calls', 0)} tokens={stats.get('prompt_tokens', 0)}/{stats.get('completion_tokens', 0)}")
    if result.get("problems"):
        parts.append(f"problems={len(result['problems'])}")
    return " ".join(parts)


# --- the steps ---------------------------------------------------------------------------


def step_segment(db, book, run: V2Run, ctx: RunContext) -> dict:
    chapters = _chapters(db, book.id)
    skip = set() if ctx.force else chapters_with_segments(db, [c.id for c in chapters])

    def work(chapter):
        segments = segment_chapter(chapter.source_text or "", chapter_id=chapter.id)
        return {"segments": store_chapter_segments(db, book_id=book.id, chapter_id=chapter.id, segments=segments)}

    walked = walk_chapters(db, book, run, ctx, chapters, work, skip=skip)
    total = sum(int(r.get("segments") or 0) for r in walked["results"].values())
    _log(db, book, f"сегментация: глав {walked['done']}, пропущено {walked['skipped']}, сегментов {total}")
    db.commit()
    return {"chapters": walked["done"], "skipped": walked["skipped"], "segments": total}


def step_cast(db, book, run: V2Run, ctx: RunContext) -> dict:
    """Book-level cast extraction through the old pipeline's `_run_char_extraction`, once.

    Only when the book has no characters: a cast that exists — imported, extracted
    earlier, or edited by hand — is the cast. A failure here is reported and the
    book goes on with whatever cast it has; the attribute step can still work with
    the narrator alone, and a person can fill the cast in later.
    """
    _touch(db, run, ctx, chapters_total=1, chapters_done=0)
    existing = db.query(Character).filter(Character.book_id == book.id).count()
    if existing and not ctx.force:
        _log(db, book, f"персонажи: используем существующие ({existing})")
        _touch(db, run, ctx, chapters_done=1)
        return {"reused": existing, "imported": 0}

    job = (
        db.query(ScriptJob)
        .filter(ScriptJob.book_id == book.id, ScriptJob.stage == "char_extraction")
        .order_by(ScriptJob.created_at.desc())
        .first()
    )
    from app.services.step_models import step_model

    # Модель — один раз на запуск извлечения и на этот запуск: повтор после смены модели на
    # странице идёт на новой, а не на той, что записана в прошлой задаче.
    provider, model = step_model("characters")
    if job is None:
        job = ScriptJob(book_id=book.id, chapter_id="", chapter_index=0, stage="char_extraction",
                        status="processing", provider=provider, model=model)
        db.add(job)
        db.flush()
    job.provider, job.model = provider, model
    job.status = "processing"
    db.commit()
    try:
        from app.services.char_extraction import _run_char_extraction

        imported = int(_run_char_extraction(db, book, job) or 0)
        job.status = "done"
        _log(db, book, f"персонажи: извлечено {imported}")
        db.commit()
        _touch(db, run, ctx, chapters_done=1)
        return {"reused": 0, "imported": imported}
    except Exception as exc:  # noqa: BLE001 — reported, not fatal
        db.rollback()
        logger.warning("v2 cast extraction failed book=%s", book.id, exc_info=True)
        job = db.get(ScriptJob, job.id) or job
        job.status = "failed"
        job.error_message = f"{type(exc).__name__}: {str(exc)[:200]}"
        _log(db, book, f"персонажи: извлечение не удалось — {job.error_message}; книга идёт дальше с текущим кастом", "warning")
        db.commit()
        _touch(db, run, ctx, chapters_done=1)
        return {"reused": 0, "imported": 0, "error": job.error_message}


def step_attribute(db, book, run: V2Run, ctx: RunContext) -> dict:
    chapters = _chapters(db, book.id)
    skip = set() if ctx.force else chapters_attributed(db, [c.id for c in chapters])
    characters = db.query(Character).filter(Character.book_id == book.id).all()
    totals = new_stats()
    problems: list[str] = []

    def work(chapter):
        # The model client is made on first use: a run whose chapters are all
        # already attributed must not fail for want of an API key.
        result = attribute_chapter_rows(
            db, book, chapter, characters, llm=ctx.get_llm(), provider=ctx.provider, model=ctx.model,
            review=ctx.review, on_batch=ctx.on_heartbeat and (lambda *_a, **_k: ctx.on_heartbeat()),
        )
        merge_stats(totals, result["stats"])
        problems.extend(f"глава {chapter.chapter_index}: {p}" for p in result["problems"])
        return result

    walked = walk_chapters(db, book, run, ctx, chapters, work, skip=skip)
    _log(db, book, f"роли: глав {walked['done']}, пропущено {walked['skipped']}, вызовов {totals['calls']}, "
                   f"токены {totals['prompt_tokens']}/{totals['completion_tokens']}, проблем {len(problems)}")
    db.commit()
    return {"chapters": walked["done"], "skipped": walked["skipped"], "stats": totals, "problems": problems}


def step_stress(db, book, run: V2Run, ctx: RunContext) -> dict:
    chapters = _chapters(db, book.id)
    skip = set() if ctx.force else chapters_stressed(db, [c.id for c in chapters])
    resolver = ctx.get_resolver(db, book)
    counts: dict[str, int] = {}
    marks_total = 0

    def work(chapter):
        nonlocal marks_total
        result = stress_chapter_rows(db, chapter, resolver)
        marks_total += int(result["marks"])
        for source, count in result["counts"].items():
            counts[source] = counts.get(source, 0) + int(count)
        return result

    walked = walk_chapters(db, book, run, ctx, chapters, work, skip=skip)
    _log(db, book, f"ударения: глав {walked['done']}, пропущено {walked['skipped']}, ударений {marks_total}, "
                   f"без ответа {counts.get('unresolved', 0)}")
    db.commit()
    return {"chapters": walked["done"], "skipped": walked["skipped"], "marks": marks_total, "counts": counts}


STEP_FUNCTIONS: dict[str, Callable] = {
    "segment": step_segment,
    "cast": step_cast,
    "attribute": step_attribute,
    "stress": step_stress,
}


# --- the run -----------------------------------------------------------------------------


def _notify(db, book, previous: str, detail: str = "") -> None:
    try:
        from app.services.telegram import notify_book_status_change

        notify_book_status_change(db, book, previous, book.status, detail=detail)
    except Exception:  # noqa: BLE001 — a notification must never fail the book
        logger.warning("v2 status notification failed book=%s", book.id, exc_info=True)


def _open_run(db, book, run_id: str | None) -> V2Run:
    run = db.get(V2Run, run_id) if run_id else None
    if run is None:
        run = V2Run(id=run_id or str(uuid.uuid4()), book_id=book.id, status="queued")
        db.add(run)
    run.status = "running"
    run.started_at = utcnow_naive()
    run.finished_at = None
    run.error = ""
    run.updated_at = run.started_at
    db.commit()
    return run


def _settle_book(db, book, context) -> None:
    """After the last step: the author's cast, and what the book costs.

    Both are consequences of the markup rather than steps of it, and both used to be
    manual: the actors sat in the author profile behind a button nobody knew to press,
    and the money came from a v1 counter that reads text v2 never writes. A failure
    here must not fail a run that has already produced the markup, so each is logged
    and stepped over.
    """
    from app.services.author_profile import apply_author_profile_to_book
    from app.v2.budget_ops import rebuild_v2_budget

    if str(getattr(book, "author_id", "") or ""):
        try:
            report = apply_author_profile_to_book(db, book)
            context.say(
                f"профиль автора: актёров {report['actors_applied']}, цветов {report['colours_applied']}"
                f" на {report['characters_linked']} ролей"
            )
            _log(db, book, "профиль автора применён: актёры {actors_applied}, цвета {colours_applied},"
                           " связано ролей {characters_linked}".format(**report))
        except Exception as exc:  # noqa: BLE001 — markup is done; this is a convenience
            logger.warning("author profile apply failed book=%s: %s", book.id, exc)
            _log(db, book, f"профиль автора не применился: {exc}", "warning")
    else:
        _log(db, book, "книга не привязана к автору — каст без актёров; привяжите автора в карточке книги")

    try:
        money = rebuild_v2_budget(db, book.id, updated_by="pipeline")
        context.say(f"смета: реплик {money['lines']}, сумма {money['total_rub']} ₽")
    except Exception as exc:  # noqa: BLE001
        logger.warning("v2 budget rebuild failed book=%s: %s", book.id, exc)
        _log(db, book, f"смета не пересчиталась: {exc}", "warning")


ACTIVE_STATUSES = ("queued", "running")
# A run whose heartbeat is older than this is a run whose process died: one chapter
# of attribution takes minutes, a heartbeat also lands after every model batch.
STALE_AFTER = timedelta(hours=2)


def latest_run(db, book_id: str) -> V2Run | None:
    return (
        db.query(V2Run)
        .filter(V2Run.book_id == book_id)
        .order_by(V2Run.updated_at.desc(), V2Run.started_at.desc())
        .first()
    )


def expire_stale_runs(db, book_id: str, *, stale_after: timedelta = STALE_AFTER, now=None) -> int:
    """Mark queued/running runs whose heartbeat stopped as failed. Returns how many."""
    cutoff = (now or utcnow_naive()) - stale_after
    rows = db.query(V2Run).filter(V2Run.book_id == book_id, V2Run.status.in_(ACTIVE_STATUSES)).all()
    expired = 0
    for run in rows:
        if run.updated_at and run.updated_at >= cutoff:
            continue
        run.status = "failed"
        run.error = "heartbeat lost: the worker stopped without finishing the run"
        run.finished_at = now or utcnow_naive()
        expired += 1
    return expired


def active_run(db, book_id: str) -> V2Run | None:
    """The run that is queued or running for the book right now, if any."""
    return (
        db.query(V2Run)
        .filter(V2Run.book_id == book_id, V2Run.status.in_(ACTIVE_STATUSES))
        .order_by(V2Run.updated_at.desc())
        .first()
    )


def create_queued_run(db, book_id: str) -> V2Run:
    """The row the API makes before enqueueing, so the progress screen sees the run at once."""
    run = V2Run(id=str(uuid.uuid4()), book_id=book_id, status="queued", updated_at=utcnow_naive())
    db.add(run)
    return run



#: шаг прогона книги → шаг журнала трат (как на странице «Нейросети»)
SPEND_STEPS = {"attribute": "attribution", "cast": "characters"}

def run_book_pipeline(
    book_id: str,
    *,
    steps=STEPS,
    force: bool = False,
    provider: str | None = None,
    model: str | None = None,
    thinking: str = "off",
    run_id: str | None = None,
    session_factory=None,
    step_functions: dict[str, Callable] | None = None,
    ctx: RunContext | None = None,
    on_progress: Callable[[str], None] | None = None,
    on_heartbeat: Callable[[], None] | None = None,
) -> V2Run:
    """Run `steps` on the book, in the order of `STEPS`, and return the finished run row.

    Ends `done` with the book in `author_review`, `stopped` when the flag was set
    between chapters, or `failed` with the error on the row and the book `failed`.
    `run_id` adopts a row the API created as `queued`, so the progress screen sees
    the run from the moment it was asked for.
    """
    from app.db import SessionLocal

    factory = session_factory or SessionLocal
    functions = step_functions or STEP_FUNCTIONS
    unknown = [s for s in steps if s not in functions]
    if unknown:
        raise ValueError(f"неизвестные шаги: {', '.join(unknown)}")
    ordered = [s for s in STEPS if s in set(steps)] + [s for s in steps if s not in STEPS]

    context = ctx or RunContext()
    context.force = bool(force) or context.force
    context.thinking = thinking or context.thinking
    context.on_progress = on_progress or context.on_progress
    context.on_heartbeat = on_heartbeat or context.on_heartbeat

    with factory() as db:
        book = db.get(ScriptBook, book_id)
        if book is None:
            raise ValueError(f"книги {book_id} нет")
        # The book's own choice of model wins over the process-wide default: it is what
        # the hub shows and what the author was asked about.
        chosen = model_catalog.for_book(book)
        context.provider = provider or context.provider or chosen.provider or settings.default_final_provider
        context.model = model or context.model or chosen.model or settings.default_final_model
        run = _open_run(db, book, run_id)
        previous_status = str(book.status or "")
        book.pipeline_mode = V2_MODE
        book.status = "processing"
        # `stop_requested` is left as it is: the API clears it when it asks for a run,
        # and a flag raised while the run sat in the queue is honoured before chapter one.
        book.last_notified_status = ""  # re-arm: one notification per run
        _log(db, book, f"старт прогона {run.id}: шаги {', '.join(ordered)}"
                       f"{' (force)' if context.force else ''}, модель {context.provider}/{context.model}")
        db.commit()

        try:
            if "attribute" in ordered and context.llm is None and functions["attribute"] is STEP_FUNCTIONS.get("attribute"):
                # Ключ разметки — до первой траты: иначе персонажи успели бы оплатиться у
                # другого провайдера, а разметка упала бы на пустом ключе.
                from app.pipeline.llm_client import _resolve_provider
                from app.services.step_models import PROVIDER_LABELS

                try:
                    key = _resolve_provider(context.provider)[0]
                except RuntimeError:
                    key = None  # незнакомый провайдер — скажет сам вызов, как раньше
                if key == "":
                    _touch(db, run, context, step="attribute", chapters_done=0)
                    label = PROVIDER_LABELS.get(context.provider, context.provider)
                    raise RuntimeError(f"Для разметки нужен ключ {label} — добавьте его в «Настройки → Нейросети».")
            for step in ordered:
                _touch(db, run, context, step=step, chapters_done=0)
                context.say(f"— {STEP_LABELS.get(step, step)} —")
                with spend.context(SPEND_STEPS.get(step, step), book_id=book.id, run_id=run.id):
                    functions[step](db, book, run, context)
            _settle_book(db, book, context)
            # A stop asked for and honoured — or asked for and overtaken by the end of
            # the run — must not outlive the run: a raised flag makes every screen say
            # «останавливается» for ever after.
            book.stop_requested = "false"
            book.status = FINAL_STATUS
            _log(db, book, "прогон завершён: книга на авторской проверке")
            _touch(db, run, context, status="done", finished_at=utcnow_naive())
            _notify(db, book, previous_status, "Пайплайн v2 завершён: все главы размечены.")
            db.commit()
        except StopRequested as exc:
            db.rollback()
            book = db.get(ScriptBook, book_id)
            book.status = "stopped"
            book.stop_requested = "false"
            _log(db, book, f"прогон остановлен: {exc}")
            _touch(db, run, context, status="stopped", error=str(exc), finished_at=utcnow_naive())
        except Exception as exc:  # noqa: BLE001 — the row must say what happened
            db.rollback()
            logger.error("v2 pipeline failed book=%s step=%s: %s", book_id, run.step, exc, exc_info=True)
            book = db.get(ScriptBook, book_id)
            book.status = "failed"
            error = f"{type(exc).__name__}: {str(exc)[:500]}"
            _log(db, book, f"прогон упал на шаге {run.step}: {error}", "error")
            _touch(db, run, context, status="failed", error=error, finished_at=utcnow_naive())
            _notify(db, book, previous_status, f"v2 · {STEP_LABELS.get(run.step, run.step)} · {error[:160]}")
            db.commit()
        db.refresh(run)
        db.expunge(run)
        return run
