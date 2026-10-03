"""What a book's progress screen shows: the run, the six steps, and the counts behind them.

The four pipeline steps are read two ways and the answers are combined. The counts
say what the database holds — segments exist, chapters carry attributions — and that
is the truth for a book that was migrated rather than run, where there is no run row
at all. The run row says what is happening now: which step is being worked on, how
far it got, whether it failed. A step the counts call done is done; the run then
only adds «running» for the step in flight, «failed» for the one it fell on, and
«skipped» for a step the run went past without producing anything (a cast
extraction that found nobody, say).

The last two steps, review and publication, are the book's own status: they are
done by people, not by the worker.
"""
from __future__ import annotations

from sqlalchemy import func

from app.models import Character, ScriptBook, ScriptChapter
from app.services.studio_settings import usd_rub_rate
from app.v2 import model_catalog
from app.v2.review_ops import approval_counts
from app.v2.models import V2Attribution, V2Segment, V2StressMark
from app.v2.pipeline import STEPS, latest_run
from app.time_utils import iso_utc

STEP_KEYS = ("segment", "cast", "attribute", "stress", "review", "publish")
STEP_LABELS = {
    "segment": "Сегментация",
    "cast": "Персонажи",
    "attribute": "Роли",
    "stress": "Ударения",
    "review": "Проверка",
    "publish": "Публикация",
}
REVIEW_DONE_STATUSES = ("author_review", "approved", "published_to_dictor")
PUBLISH_DONE_STATUSES = ("published_to_dictor",)


def _iso(value) -> str | None:
    return iso_utc(value)


def book_counts(db, book_id: str) -> dict:
    """`{chapters, segments, attributed_chapters, stressed_chapters, cast}` for the book."""
    chapters = db.query(func.count(ScriptChapter.id)).filter(ScriptChapter.book_id == book_id).scalar() or 0
    segments = db.query(func.count(V2Segment.id)).filter(V2Segment.book_id == book_id).scalar() or 0
    attributed = (
        db.query(func.count(func.distinct(V2Segment.chapter_id)))
        .join(V2Attribution, V2Attribution.segment_id == V2Segment.id)
        .filter(V2Segment.book_id == book_id)
        .scalar()
    ) or 0
    stressed = (
        db.query(func.count(func.distinct(V2Segment.chapter_id)))
        .join(V2StressMark, V2StressMark.segment_id == V2Segment.id)
        .filter(V2Segment.book_id == book_id)
        .scalar()
    ) or 0
    cast = db.query(func.count(Character.id)).filter(Character.book_id == book_id).scalar() or 0
    return {
        "chapters": int(chapters),
        "segments": int(segments),
        "attributed_chapters": int(attributed),
        "stressed_chapters": int(stressed),
        "cast": int(cast),
    }


def run_payload(run) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "status": run.status,
        "step": run.step or "",
        "chapters_total": int(run.chapters_total or 0),
        "chapters_done": int(run.chapters_done or 0),
        "calls": int(run.calls or 0),
        "tokens": {"prompt": int(run.prompt_tokens or 0), "completion": int(run.completion_tokens or 0)},
        "started_at": _iso(run.started_at),
        "updated_at": _iso(run.updated_at),
        "finished_at": _iso(run.finished_at),
        "error": run.error or "",
    }


def _pipeline_step_states(counts: dict, run) -> dict[str, tuple[str, str]]:
    """`{key: (state, detail)}` for the four worker steps."""
    chapters = counts["chapters"]
    done_by_counts = {
        "segment": counts["segments"] > 0,
        "cast": counts["cast"] > 0,
        "attribute": chapters > 0 and counts["attributed_chapters"] >= chapters,
        "stress": chapters > 0 and counts["stressed_chapters"] >= chapters,
    }
    details = {
        "segment": f"сегментов {counts['segments']}",
        "cast": f"персонажей {counts['cast']}",
        "attribute": f"глав {counts['attributed_chapters']}/{chapters}",
        "stress": f"глав {counts['stressed_chapters']}/{chapters}",
    }
    states = {key: ("done" if done_by_counts[key] else "pending", details[key]) for key in STEPS}
    if run is None or run.step not in STEPS:
        return states

    current = STEPS.index(run.step)
    for index, key in enumerate(STEPS):
        state, detail = states[key]
        if index < current or (index == current and run.status == "done"):
            if state != "done":
                states[key] = ("skipped", detail)
            continue
        if index > current:
            continue
        # The step the run is on, or fell on, or was stopped on.
        if run.status in ("running", "queued"):
            progress = f"{int(run.chapters_done or 0)}/{int(run.chapters_total or 0)}"
            states[key] = ("running", f"{detail} · идёт {progress}")
        elif run.status == "failed":
            states[key] = ("failed", (run.error or "").strip()[:200] or detail)
        elif run.status == "stopped" and state != "done":
            states[key] = ("pending", f"{detail} · остановлено")
    return states


def _status_step_states(book_status: str, review_counts: dict) -> dict[str, tuple[str, str]]:
    """Review and publication: the book's status, told through the chapters under it.

    «статус author_review» said nothing about how far the reading had got. The
    chapters do: the review step is done when every marked-up chapter carries the
    author's mark, and running while some of them do.
    """
    status = str(book_status or "").strip()
    approved = int(review_counts.get("approved") or 0)
    attributed = int(review_counts.get("attributed") or 0)
    published = int(review_counts.get("published") or 0)

    if attributed and approved >= attributed:
        review = ("done", f"проверено {approved} из {attributed}")
    elif approved:
        review = ("running", f"проверено {approved} из {attributed}")
    elif status in REVIEW_DONE_STATUSES:
        review = ("pending", f"ждёт проверки автором · глав {attributed}")
    else:
        review = ("pending", f"статус {status or '—'}")

    if published and attributed and published >= attributed:
        publish = ("done", f"дикторам отдано глав {published}")
    elif published:
        publish = ("running", f"опубликовано {published} из {attributed}")
    elif status in PUBLISH_DONE_STATUSES:
        publish = ("done", "опубликовано")
    else:
        publish = ("pending", "после проверки")
    return {"review": review, "publish": publish}


def book_progress(db, book_id: str) -> dict | None:
    """The progress payload for one book; None when the book does not exist."""
    book = db.get(ScriptBook, str(book_id or "").strip())
    if book is None:
        return None
    run = latest_run(db, book.id)
    counts = book_counts(db, book.id)
    review_counts = approval_counts(db, book.id)
    states = {**_pipeline_step_states(counts, run), **_status_step_states(book.status, review_counts)}
    steps = [
        {"key": key, "label": STEP_LABELS[key], "state": states[key][0], "detail": states[key][1]}
        for key in STEP_KEYS
    ]
    chosen = model_catalog.for_book(book)
    last = run_payload(run)
    rate = usd_rub_rate(db)
    # Priced at the tariff that was in force when the run started: DeepSeek charges
    # twice as much inside its peak window, and a book run is over in hours.
    peak = model_catalog.is_peak(getattr(run, "started_at", None))
    tokens = (last or {}).get("tokens") or {}
    cost = (
        chosen.cost_rub(int(tokens.get("prompt") or 0), int(tokens.get("completion") or 0), rate, peak=peak)
        if last else None
    )
    return {
        "book_id": book.id,
        # The model this book runs on, and what the last run on it cost.
        "model": {
            **chosen.as_dict(rate),
            "choices": [item.as_dict(rate) for item in model_catalog.CATALOG],
            "usd_rub_rate": round(rate, 2),
            "last_run_cost_rub": round(cost, 2) if cost is not None else None,
            "last_run_peak": bool(peak) if last else None,
        },
        "mode": str(book.pipeline_mode or "standard"),
        "status": str(book.status or ""),
        "stop_requested": str(book.stop_requested or "").lower() == "true",
        "run": run_payload(run),
        "steps": steps,
        "counts": counts,
        # how far the author has got through the chapters
        "review": review_counts,
        # an approved chapter opens for recording without waiting for the button
        "auto_publish": str(getattr(book, "auto_publish", "") or "").strip().lower() == "true",
    }
