"""Повторная проверка доступа не позволяет переслать чужому человеку старую ссылку."""
from pathlib import PurePosixPath
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app.auth import is_authenticated, session_payload
from app.db import SessionLocal
from app.services.narrator_audit_export import ExportError, archive_chunks, build_archive, build_labels, current_export


def _error(error):
    return JSONResponse({"ok": False, "error": error.code}, status_code=error.status,
                        headers={"Cache-Control": "private, no-store"})


def api_v2_narrator_audit(request: Request, chapter_id: str):
    if not is_authenticated(request):
        return _error(ExportError("unauthorized", 401))
    try:
        with SessionLocal() as db:
            payload, _ = current_export(db, str(session_payload(request).get("uid") or ""), chapter_id)
        if request.query_params.get("view") == "labels":
            # Без временных границ замечание остаётся в проверке, а пустой TXT в описи не нужен.
            payload = {"labels_by_take": {
                item["audio_file_id"]: item["labels_download_url"] + "?name=asr"
                for item in payload["items"]
                if any(finding["markers"] for finding in item["findings"])
            }}
        return JSONResponse(payload, headers={"Cache-Control": "private, no-store"})
    except ExportError as error:
        return _error(error)


def api_v2_narrator_audit_download(request: Request, chapter_id: str, audit_id: str, audio_id: str):
    if not is_authenticated(request):
        return _error(ExportError("unauthorized", 401))
    archive = None
    try:
        with SessionLocal() as db:
            payload, sources = current_export(db, str(session_payload(request).get("uid") or ""), chapter_id)
            if str(payload["audit_id"] or "") != str(audit_id):
                raise ExportError("audit_not_current")
            item = next((item for item in payload["items"] if item["audio_file_id"] == str(audio_id)), None)
            if item is None:
                raise ExportError("not_found", 404)
            source = dict(sources[str(audio_id)])
        archive = build_archive(source, item, audit_id=audit_id, fingerprint=payload["fingerprint"])
        # Упаковка большого WAV длится минуты: свежая сессия видит смену каста,
        # прав и текста, произошедшую после первого чтения. Старый архив закрывается.
        with SessionLocal() as db:
            fresh, fresh_sources = current_export(db, str(session_payload(request).get("uid") or ""), chapter_id)
            fresh_item = next((entry for entry in fresh["items"] if entry["audio_file_id"] == str(audio_id)), None)
            if (fresh["audit_id"] != payload["audit_id"] or fresh["fingerprint"] != payload["fingerprint"]
                    or fresh_item != item or fresh_sources.get(str(audio_id)) != source):
                raise ExportError("audit_not_current")
        return StreamingResponse(archive_chunks(archive), media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="narrator-audit.zip"',
                     "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
            background=BackgroundTask(archive.close))
    except ExportError as error:
        if archive is not None:
            archive.close()
        return _error(error)
    except Exception:
        if archive is not None:
            archive.close()
        raise


def api_v2_narrator_audit_labels(request: Request, chapter_id: str, audit_id: str, audio_id: str):
    if not is_authenticated(request):
        return _error(ExportError("unauthorized", 401))
    try:
        user_id = str(session_payload(request).get("uid") or "")
        with SessionLocal() as db:
            payload, _ = current_export(db, user_id, chapter_id)
            if str(payload["audit_id"] or "") != str(audit_id):
                raise ExportError("audit_not_current")
            item = next((entry for entry in payload["items"] if entry["audio_file_id"] == str(audio_id)), None)
            if item is None:
                raise ExportError("not_found", 404)
        labels = build_labels(item)
        # Даже короткая подготовка TXT не отменяет свежую проверку назначения и сверки.
        with SessionLocal() as db:
            fresh, _ = current_export(db, user_id, chapter_id)
            fresh_item = next((entry for entry in fresh["items"] if entry["audio_file_id"] == str(audio_id)), None)
            if (fresh["audit_id"] != payload["audit_id"] or fresh["fingerprint"] != payload["fingerprint"]
                    or fresh_item != item):
                raise ExportError("audit_not_current")
        filename = ("narrator-labels.txt" if request.query_params.get("name") == "asr"
                    else PurePosixPath(item["filename"]).stem + "_labels.txt")
        return Response(labels, media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="narrator-labels.txt"; '
                                 + "filename*=UTF-8''" + quote(filename, safe=""),
                                 "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    except ExportError as error:
        return _error(error)
