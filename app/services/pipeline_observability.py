from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from app.models import PipelineEvent, PipelineRun, ScriptBook, ScriptJob, ScriptLog
from app.time_utils import utcnow_naive


ERROR_CODE_PATTERNS: list[tuple[str, str]] = [
    ("fountain too short", "FINAL_VALIDATION_TOO_SHORT"),
    ("forbidden markdown list", "FINAL_VALIDATION_MARKDOWN_LIST"),
    ("malformed bracketed line", "FINAL_VALIDATION_MALFORMED_BRACKET"),
    ("unsupported_country_region_territory", "PROVIDER_GEO_RESTRICTED"),
    ("country, region, or territory not supported", "PROVIDER_GEO_RESTRICTED"),
    ("worker restart", "WORKER_RESTART_INTERRUPTED"),
    ("work-horse terminated unexpectedly", "WORKER_RESTART_INTERRUPTED"),
]


def json_dumps(payload: dict[str, Any] | None) -> str:
    return json.dumps(payload or {}, ensure_ascii=False)


def ensure_pipeline_run(
    db,
    *,
    book: ScriptBook,
    triggered_by: str = "system",
    start_reason_code: str = "RUN_STARTED",
    start_reason: str = "pipeline run started",
) -> PipelineRun:
    current_id = str(book.current_pipeline_run_id or "").strip()
    if current_id:
        active = db.query(PipelineRun).filter(PipelineRun.id == current_id).first()
        if active and active.status == "running":
            return active

    previous_id = current_id
    run = PipelineRun(
        id=str(uuid.uuid4()),
        book_id=book.id,
        status="running",
        trigger_type=triggered_by,
        started_at=utcnow_naive(),
        ended_reason_code=start_reason_code,
        ended_reason_text=start_reason,
    )
    db.add(run)
    book.previous_pipeline_run_id = previous_id
    book.current_pipeline_run_id = run.id
    book.status = "processing"
    book.last_notified_status = ""  # re-arm: this fresh run may emit one outcome notification
    db.add(
        PipelineEvent(
            book_id=book.id,
            pipeline_run_id=run.id,
            event_type="run_started",
            level="info",
            code=start_reason_code,
            message=start_reason,
            details_json=json_dumps({"triggered_by": triggered_by}),
        )
    )
    return run


def log_pipeline_event(
    db,
    *,
    book_id: str,
    pipeline_run_id: str,
    event_type: str,
    message: str,
    level: str = "info",
    code: str = "",
    stage: str = "",
    chapter_id: str = "",
    chapter_index: int = 0,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        PipelineEvent(
            book_id=book_id,
            pipeline_run_id=pipeline_run_id,
            event_type=event_type,
            chapter_id=chapter_id,
            chapter_index=int(chapter_index or 0),
            stage=stage,
            level=level,
            code=code,
            message=message[:1000],
            details_json=json_dumps(details),
        )
    )


def enforce_hotfix_guardrails(
    db,
    *,
    book: ScriptBook,
    max_manual_actions_24h: int = 6,
) -> dict[str, Any]:
    from app.models import OperatorIntervention

    since = utcnow_naive() - timedelta(hours=24)
    actions_24h = (
        db.query(OperatorIntervention)
        .filter(OperatorIntervention.book_id == book.id, OperatorIntervention.created_at >= since)
        .count()
    )
    needs_investigation = actions_24h > int(max_manual_actions_24h)
    if needs_investigation:
        book.needs_investigation = "true"
        if book.status in {"queued", "processing"}:
            book.status = "stalled"
    return {"manual_actions_24h": int(actions_24h), "needs_investigation": bool(needs_investigation)}
