import json
import re

from sqlalchemy import func

from app.models import AuditLog, LlmUsageLog, ScriptLog, TelegramAuthAccount, User
from app.services.audit import audit_action_label
from app.services.llm_catalog import calc_llm_cost_usd


_FINAL_RUNTIME_RE = re.compile(r"FINAL runtime base=(\d+)ms(?: retry=(\d+)ms total=\d+ms)?", re.IGNORECASE)


def _collect_final_runtime_by_stage(db) -> dict[str, int]:
    totals = {"final_base": 0, "final_retry": 0}
    rows = db.query(ScriptLog.message).filter(ScriptLog.message.like("%FINAL runtime base=%")).all()
    for (message,) in rows:
        text = str(message or "")
        matched = _FINAL_RUNTIME_RE.search(text)
        if not matched:
            continue
        base_ms = int(matched.group(1) or 0)
        retry_ms = int(matched.group(2) or 0)
        totals["final_base"] += base_ms
        totals["final_retry"] += retry_ms
    return totals


def _safe_payload_json(raw_value: str | None) -> dict:
    try:
        payload = json.loads(raw_value or "{}")
    except (TypeError, ValueError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _empty_token_usage() -> dict[str, float | int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}


def _build_recent_activity(db) -> list[dict]:
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(200).all()
    user_ids = sorted({str(item.user_id or "").strip() for item in rows if str(item.user_id or "").strip()})
    user_names: dict[str, str] = {}
    if user_ids:
        user_names = {
            str(user.id): (str(user.display_name or user.login or user.id))
            for user in db.query(User).filter(User.id.in_(user_ids)).all()
        }
    recent_activity = []
    for item in rows:
        recent_activity.append(
            {
                "action": item.action,
                "action_label": audit_action_label(item.action),
                "entity_type": item.entity_type,
                "entity_id": item.entity_id,
                "actor_name": user_names.get(item.user_id, item.user_id or "system"),
                "payload": _safe_payload_json(item.payload_json),
                "created_at": item.created_at,
            }
        )
    return recent_activity


def _build_usage_by_actor(db) -> list[dict]:
    return [
        {
            "created_by_name": row[0] or "Неизвестно",
            "book_count": int(row[1] or 0),
            "prompt_tokens": int(row[2] or 0),
            "completion_tokens": int(row[3] or 0),
            "total_tokens": int(row[4] or 0),
            "cost_usd": calc_llm_cost_usd(int(row[2] or 0), int(row[3] or 0)),
        }
        for row in db.query(
            LlmUsageLog.created_by_name,
            func.count(func.distinct(LlmUsageLog.book_id)),
            func.sum(LlmUsageLog.prompt_tokens),
            func.sum(LlmUsageLog.completion_tokens),
            func.sum(LlmUsageLog.total_tokens),
        ).group_by(LlmUsageLog.created_by_name).order_by(func.sum(LlmUsageLog.total_tokens).desc()).all()
    ]


def _build_usage_by_stage(db, runtime_by_stage: dict[str, int]) -> list[dict]:
    usage_stage_rows = db.query(
        LlmUsageLog.stage,
        func.count(LlmUsageLog.id),
        func.count(func.distinct(LlmUsageLog.book_id)),
        func.sum(LlmUsageLog.prompt_tokens),
        func.sum(LlmUsageLog.completion_tokens),
        func.sum(LlmUsageLog.total_tokens),
    ).filter(
        LlmUsageLog.stage.in_(("final_base", "final_retry"))
    ).group_by(LlmUsageLog.stage).all()
    usage_by_stage_map = {
        str(row[0] or ""): {
            "stage": str(row[0] or ""),
            "request_count": int(row[1] or 0),
            "book_count": int(row[2] or 0),
            "prompt_tokens": int(row[3] or 0),
            "completion_tokens": int(row[4] or 0),
            "total_tokens": int(row[5] or 0),
            "cost_usd": calc_llm_cost_usd(int(row[3] or 0), int(row[4] or 0)),
            "runtime_ms": int(runtime_by_stage.get(str(row[0] or ""), 0) or 0),
        }
        for row in usage_stage_rows
    }
    return [
        usage_by_stage_map.get(
            stage,
            {
                "stage": stage,
                "request_count": 0,
                "book_count": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "runtime_ms": int(runtime_by_stage.get(stage, 0) or 0),
            },
        )
        for stage in ("final_base", "final_retry")
    ]


def _build_latest_book_tokens(db, latest_book) -> dict[str, float | int]:
    if not latest_book:
        return _empty_token_usage()
    latest_usage = db.query(
        func.sum(LlmUsageLog.prompt_tokens),
        func.sum(LlmUsageLog.completion_tokens),
        func.sum(LlmUsageLog.total_tokens),
    ).filter(LlmUsageLog.book_id == latest_book.id).first()
    prompt_tokens = int((latest_usage[0] if latest_usage else 0) or 0)
    completion_tokens = int((latest_usage[1] if latest_usage else 0) or 0)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": int((latest_usage[2] if latest_usage else 0) or 0),
        "cost_usd": calc_llm_cost_usd(prompt_tokens, completion_tokens),
    }


def build_owner_dashboard_data(db, latest_book) -> dict:
    owner_whitelist_accounts = db.query(TelegramAuthAccount).order_by(
        TelegramAuthAccount.display_name.asc(),
        TelegramAuthAccount.telegram_user_id.asc(),
    ).all()

    runtime_by_stage = _collect_final_runtime_by_stage(db)

    return {
        "owner_whitelist_accounts": owner_whitelist_accounts,
        "recent_activity": _build_recent_activity(db),
        "usage_by_actor": _build_usage_by_actor(db),
        "usage_by_stage": _build_usage_by_stage(db, runtime_by_stage),
        "latest_book_tokens": _build_latest_book_tokens(db, latest_book),
    }
