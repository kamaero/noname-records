"""Статусы читают подтверждения зеркала, не запускают сетевую проверку из HTTP."""
from __future__ import annotations

from app.time_utils import iso_utc
from app.config import settings
from app.services.mirror_evidence import mirror_proof_valid


_MIRROR_ERRORS = {
    "mismatch": "NAS checksum mismatch",
    "missing": "NAS copy is missing",
    "error": "NAS copy failed",
    "failed": "NAS copy failed",
}


def nas_delivery(files: list[dict], *, configured: bool) -> dict:
    """Каждый дубль и исправление учитываются отдельно, даже с одинаковым именем."""
    distinct = {str(item["audio_id"]): item for item in files}
    entries = []
    for audio_id, item in distinct.items():
        mirror_state = str(item.get("mirror_state") or "")
        verify_state = str(item.get("verify_state") or "")
        error = _MIRROR_ERRORS.get(mirror_state, "")
        if not error and verify_state in {"missing", "mismatch"}:
            error = "Source file is missing" if verify_state == "missing" else "Source checksum mismatch"
        verified_at = iso_utc(item.get("mirrored_at")) or None
        if not configured:
            state, error = "unknown", ""
        elif error:
            state = "error"
        elif verified_at and mirror_state == "ok" and mirror_proof_valid(item, root=settings.audio_nas_path):
            state = "delivered"
        elif verified_at and mirror_state == "ok":
            state = "unknown"
        else:
            state = "pending"
        # Входное имя может содержать путь с любой ОС: наружу идёт только имя.
        filename = str(item.get("filename") or "").replace("\\", "/").rsplit("/", 1)[-1]
        entries.append({"audio_id": audio_id, "role": item["role"], "filename": filename,
                        "state": state, "verified_at": verified_at, "error": error})
    entries.sort(key=lambda item: (item["role"], item["filename"], item["audio_id"]))
    total = len(entries)
    delivered = sum(item["state"] == "delivered" for item in entries)
    # Округление вниз не превращает 999 подтверждений из 1000 в 100%.
    percent = (delivered * 100 // total) if total and configured else None
    return {
        "state": "empty" if not total else "known" if configured else "unknown",
        "total_files": total, "delivered_files": delivered, "percent": percent,
        "checked_at": max((item["verified_at"] for item in entries if item["verified_at"]), default=None),
        "reason": "no_files" if not total else "" if configured else "nas_not_configured",
        "files": entries,
    }


def assembly_status(chapter, *, configured: bool) -> dict:
    """Отметка скачивания или готовность ролей не доказывают создание архива."""
    if not configured and not getattr(chapter, "session_key", ""):
        state, reason = "unknown", "nas_not_configured"
    elif chapter.session_outdated_at:
        state, reason = "outdated", "chapter_changed"
    elif not chapter.session_archived_at:
        state, reason = "missing", "not_assembled"
    elif not str(chapter.session_sha256 or "").strip():
        state, reason = "unknown", "archive_not_verified"
    else:
        state, reason = "current", ""
    return {"assembled": state == "current", "state": state,
            "completed_at": iso_utc(chapter.session_archived_at) or None, "reason": reason}
