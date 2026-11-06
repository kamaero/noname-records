"""Оплаченный аудит начинается только с сохранённого события полной сборки.

Очередь консилиума может ждать часы: возраст задания не разрешает платить повторно.
Резервирование одной SQL-командой закрывает гонку между скачиванием ZIP и NAS-фоном.
"""
from __future__ import annotations

import json
import logging
import math
import os
from uuid import uuid4

from sqlalchemy import and_, case, exists, func, insert, literal, or_, select, update

from app.config import settings
from app.db import SessionLocal
from app.models import AsrAudit, AudioFile, BackgroundRun, ScriptChapter
from app.redis_client import get_queue
from app.time_utils import utcnow_naive

logger = logging.getLogger(__name__)


class AuditUnavailable(RuntimeError):
    """Платный прогон не поставлен в очередь; причина пригодна для API."""


def audit_assembly_snapshot(db, chapter_id: str, *, book_status: dict | None = None) -> dict | None:
    try:
        return _audit_assembly_snapshot(db, chapter_id, book_status=book_status)
    except Exception:
        logger.exception("Не удалось проверить состав главы %s перед аудитом", chapter_id)
        return None


def _audit_assembly_snapshot(db, chapter_id: str, *, book_status: dict | None = None) -> dict | None:
    from app.services.asr_audit import latest_narrator_take, narrator_input_fingerprint, narrator_lines
    from app.services.chapter_delivery import (chapter_recording_status, build_chapter_session,
                                               session_fingerprint)
    from app.services.audio_integrity import chapter_has_broken_files

    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if chapter is None or chapter_has_broken_files(db, chapter.id):
        return None
    # Книжная сводка связывает старые записи по названию главы. Для разрешения
    # оплаты нужны точный scope и реальные клипы, а не чужой одноимённый файл.
    row = chapter_recording_status(db, chapter.id)
    if not row or not row["ready"] or not row["total_lines"]:
        return None
    take = latest_narrator_take(db, chapter)
    # Полная глава из одних заголовков/ремарок законно собирается, но рассказчик
    # не должен оплачивать проверку текста, который ему не требуется читать.
    if take is None or not narrator_lines(db, chapter.id)["required"] or not _assembly_sources_available(db, chapter.id):
        return None
    # Метки самого аудита не меняют вход платной проверки и не создают цикл сборок.
    xml = build_chapter_session(db, chapter.id, relative=True, include_audit=False)
    if not xml:
        return None
    narrator_fingerprint = narrator_input_fingerprint(db, chapter.id)
    if not isinstance(narrator_fingerprint, str) or not narrator_fingerprint.strip():
        return None
    return {"assembled_fingerprint": session_fingerprint(xml), "narrator_take_id": str(take.id),
            "narrator_input_fingerprint": narrator_fingerprint}



def _assembly_addresses_complete(db, chapter_id: str, *, layout=None) -> bool:
    """Полнота сценария не зависит от доступности диска или настроенного NAS."""
    from app.services.chapter_delivery import _script_layout
    from app.services.asr_run import chapter_replicas
    from app.services.asr_audit import classify_narrator_line
    from app.services.author_profile import normalize_name
    from app.services.shared_runtime import is_narrator_name

    layout = layout if layout is not None else _script_layout(db, chapter_id)
    if layout is None or not layout[3]:
        return False
    mandatory = set()
    for replica in chapter_replicas(db, chapter_id):
        if is_narrator_name(replica["role"]) and classify_narrator_line(replica["text"]) != "required":
            continue
        mandatory.add((normalize_name(replica["role"]), str(replica["segment_id"]), int(replica["role_index"])))
    placed = set()
    for role, clip in layout[3]:
        if (clip.role_index is None or not math.isfinite(clip.source_in) or not math.isfinite(clip.source_out)
                or clip.source_in < 0 or clip.source_out <= clip.source_in):
            continue
        placed.add((normalize_name(role), str(clip.segment_id), int(clip.role_index)))
    return bool(placed) and mandatory.issubset(placed)


def _assembly_sources_available(db, chapter_id: str) -> bool:
    """Старая отметка md5 не доказывает, что файл всё ещё доступен платному аудиту."""
    from app.services import audio_storage, nas_health
    from app.services.chapter_delivery import _script_layout
    from app.services.audio_naming import book_token
    from app.services.audio_uploads import derive_book_code

    layout = _script_layout(db, chapter_id)
    if layout is None or not _assembly_addresses_complete(db, chapter_id, layout=layout):
        return False
    selected = {str(clip.audio_file_id) for _role, clip in layout[3]}
    files = db.query(AudioFile).filter(AudioFile.id.in_(selected)).all()
    if len(files) != len(selected):
        return False
    book = layout[1]
    expected_code = book_token(derive_book_code(str(getattr(book, "title", "") or "")))
    # Старая публичная схема связывает файлы с книгами только через код; чужой
    # исходник мог попасть в раскладку из-за одинаковых названий глав.
    if not expected_code or any(book_token(str(item.book_code or "")) != expected_code for item in files):
        return False
    # У hard-NFS даже stat/open зависает при отвалившемся NAS. Сначала сторож,
    # затем любые обращения к исходникам; resolve_path сам диск не трогает.
    if any(str(item.location or "local") == "nas" for item in files):
        online = nas_health.nas_online()
        # У RQ нет веб-сторожа: None — отсутствие знания в этом процессе,
        # а не отказ NAS. Разовая проба ограничена таймаутом; обработчики API
        # синхронные и выполняются FastAPI в пуле потоков, вне event loop.
        if online is False or (online is None and not nas_health.reachable_now(timeout=5.0)):
            return False
    for item in files:
        path = audio_storage.resolve_path(str(item.stored_key or ""), location=str(item.location or "local"))
        try:
            if not os.path.isfile(path):
                return False
            with open(path, "rb") as source:
                if not source.read(1):
                    return False
        except OSError:
            return False
    return True


def record_audit_assembly(db, chapter_id: str, *, book_status: dict | None = None) -> bool:
    """Вызывается только после реально записанного полного ZIP или проекта на NAS."""
    snapshot = audit_assembly_snapshot(db, chapter_id, book_status=book_status)
    if snapshot is None:
        return False
    chapter = db.get(ScriptChapter, str(chapter_id))
    chapter.audit_assembly_fingerprint = snapshot["assembled_fingerprint"]
    chapter.audit_assembly_take_id = snapshot["narrator_take_id"]
    chapter.audit_narrator_fingerprint = snapshot["narrator_input_fingerprint"]
    db.add(chapter)
    return True


def assembled_audit_snapshot(db, chapter_id: str) -> dict | None:
    snapshot = audit_assembly_snapshot(db, chapter_id)
    chapter = db.get(ScriptChapter, str(chapter_id or "").strip())
    if snapshot is None or chapter is None:
        return None
    if (chapter.audit_assembly_fingerprint != snapshot["assembled_fingerprint"]
            or chapter.audit_assembly_take_id != snapshot["narrator_take_id"]
            or chapter.audit_narrator_fingerprint != snapshot["narrator_input_fingerprint"]):
        return None
    return snapshot


def enqueue_audit(chapter_id: str, *, force: bool = False) -> str | None:
    if not settings.asr_audit_enabled:
        return None
    chapter_id = str(chapter_id or "").strip()
    with SessionLocal() as db:
        snapshot = assembled_audit_snapshot(db, chapter_id)
        if snapshot is None:
            return None
        from app.services.provider_keys import provider_key
        if not provider_key("routerai"):
            raise AuditUnavailable("audit_missing_key")
        run_key = f"asr-audit:{chapter_id}"
        run_id, now = str(uuid4()), utcnow_naive()
        meta = dict(snapshot, chapter_id=chapter_id, force=bool(force), payment_state="not_started")
        active = exists(select(BackgroundRun.id).where(
            BackgroundRun.run_key == run_key, BackgroundRun.status.in_(["queued", "running"])))
        values = {
            "id": run_id, "run_key": run_key, "job_kind": "asr_audit", "entity_type": "script_chapter",
            "entity_id": chapter_id, "status": "queued", "thread_name": "rq:consilium",
            "meta_json": json.dumps(meta, ensure_ascii=False), "created_at": now, "updated_at": now,
            "heartbeat_at": now, "error_message": "",
        }
        allowed = ~active
        if not force:
            # skipped бывает и после оплаты. Разрешение на автоматический повтор
            # даёт только явная сохранённая причина остановки ДО платного шага.
            run_meta = case((func.json_valid(BackgroundRun.meta_json), BackgroundRun.meta_json), else_="{}")
            audit_meta = case((func.json_valid(AsrAudit.lines_json), AsrAudit.lines_json), else_="{}")
            run_fingerprint = func.json_extract(run_meta, "$.narrator_input_fingerprint")
            safe_skip = and_(
                BackgroundRun.status == "skipped",
                func.coalesce(func.json_extract(run_meta, "$.payment_state"), "unknown") == "not_started",
                func.coalesce(func.json_extract(run_meta, "$.skip_reason"), "unknown").in_(
                    ["assembly_changed_before_payment", "narrator_input_changed", "no_narrator_take"]),
            )
            prior = exists(select(BackgroundRun.id).where(
                BackgroundRun.run_key == run_key, ~safe_skip,
                or_(run_fingerprint == snapshot["narrator_input_fingerprint"], run_fingerprint.is_(None)),
            ))
            known_unpaid_audit = and_(
                AsrAudit.status == "skipped",
                AsrAudit.error_message.in_(["narrator_input_changed", "no_narrator_take"]),
                func.coalesce(func.json_extract(audit_meta, "$.stt_calls"), 0) == 0,
                AsrAudit.stt_cost_rub == 0,
            )
            prior_audit = exists(select(AsrAudit.id).where(
                AsrAudit.chapter_id == chapter_id,
                func.json_extract(audit_meta, "$.narrator_input_fingerprint") == snapshot["narrator_input_fingerprint"],
                ~known_unpaid_audit,
            ))
            allowed = allowed & ~prior & ~prior_audit
        stmt = insert(BackgroundRun).from_select(list(values), select(
            *(literal(value) for value in values.values())).where(allowed))
        reserved = db.execute(stmt).rowcount
        db.commit()
        if not reserved:
            return None
        # Лимит проверяется до передачи задачи в очередь: даже фоновая сборка не
        # должна обходить установленный владельцем месячный предел.
        try:
            from app.services import spend
            decision = spend.check_start(db, estimate_rub=0, unknown_price=True, override=False,
                                         is_admin=False, actor_uid="", what="проверка рассказчика")
            if not decision.allowed:
                raise AuditUnavailable("audit_over_limit")
        except Exception as exc:
            db.rollback()
            record_payment_boundary(db, run_id, state="not_started", skip_reason="budget_blocked")
            db.execute(update(BackgroundRun).where(BackgroundRun.id == run_id).values(
                status="skipped", error_message="audit_budget_not_approved"))
            db.commit()
            if isinstance(exc, AuditUnavailable):
                raise
            raise AuditUnavailable("audit_budget_unavailable") from exc
    try:
        job = get_queue("consilium").enqueue(
            "app.worker_tasks.perform_asr_audit_task", chapter_id=chapter_id,
            run_id=run_id, **snapshot, force=bool(force), job_timeout=3600 * 4, result_ttl=86400)
        return job.id
    except Exception:
        # Падение между принятием Redis и ответом клиенту неоднозначно: резерв остаётся.
        logger.exception("Не подтверждена постановка аудита главы %s; повтор автоматически запрещён", chapter_id)
        with SessionLocal() as db:
            db.execute(update(BackgroundRun).where(BackgroundRun.id == run_id).values(
                error_message="queue_outcome_unknown: требуется проверка владельцем"))
            db.commit()
        return None


def claim_audit_run(db, run_id: str) -> bool:
    """Повтор доставки RQ не запускает уже начатый оплачиваемый шаг второй раз."""
    if not run_id:
        return False
    now = utcnow_naive()
    changed = db.execute(update(BackgroundRun).where(
        BackgroundRun.id == run_id, BackgroundRun.job_kind == "asr_audit", BackgroundRun.status == "queued",
    ).values(status="running", started_at=now, heartbeat_at=now)).rowcount
    db.commit()
    return bool(changed)



def record_payment_boundary(db, run_id: str, *, state: str, skip_reason: str = "") -> None:
    """Сохраняется до сетевого вызова: авария процесса оставляет исход неизвестным."""
    run = db.get(BackgroundRun, run_id)
    if run is None:
        return
    try:
        meta = json.loads(run.meta_json or "{}")
    except (ValueError, TypeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    meta.update(payment_state=state, skip_reason=skip_reason)
    run.meta_json = json.dumps(meta, ensure_ascii=False)
    db.add(run)
    db.commit()


def current_audit(db, chapter_id: str):
    """Метки берём только из завершённого аудита нынешнего текста и исходных клипов."""
    from app.services.asr_audit import narrator_input_fingerprint

    audits = db.query(AsrAudit).filter(
        AsrAudit.chapter_id == str(chapter_id), AsrAudit.status == "done",
    ).order_by(AsrAudit.created_at.desc(), AsrAudit.id.desc()).all()
    if not audits:
        return None
    fingerprint = narrator_input_fingerprint(db, chapter_id)
    if not fingerprint:
        return None
    for audit in audits:
        try:
            meta = json.loads(audit.lines_json or "{}")
        except (ValueError, TypeError):
            continue
        if isinstance(meta, dict) and meta.get("narrator_input_fingerprint") == fingerprint:
            return audit
    return None
