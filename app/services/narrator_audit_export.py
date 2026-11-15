"""Экспорт не меняет исходник: приблизительные метки импортируются отдельно.

Ссылку нельзя считать разрешением: за время между показом и скачиванием могут
смениться диктор, исправление или сценарий. Поэтому доступ и отпечаток проверяются
заново перед каждым архивом, а времена сверяются с исходными клипами текущей главы.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import re
import shutil
import tempfile
import threading
import zipfile
from urllib.parse import quote

from app.config import settings
from app.models import AudioFile, BookBudget, Character, ScriptChapter, TelegramAuthAccount, User, UserRole
from app.services import audio_storage
from app.services.author_profile import normalize_name
from app.services.role_identity import book_roles
from app.v2.cast_ops import approved_actor

INSTRUCTIONS = (
    "Open your original recording in Audacity and import its downloaded TXT labels. "
    "Alternatively, extract the ZIP into a new folder and open source.wav. "
    "Choose File > Import > Labels and the TXT file (labels.txt in the ZIP). "
    "Labels appear after import; they are approximate listening guides, not exact word boundaries. "
    "Times are measured from the start of the named original recording, not the chapter mix. "
    "Full context is shown on the page and in findings.txt. Save corrections as a new file; "
    "do not replace the original recording."
)


class ExportError(ValueError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


def _key(name):
    return normalize_name(str(name or ""))


def _owner(db, name):
    """Нечёткий подбор адресата письма не годится для выдачи приватного WAV."""
    wanted = _key(name)
    if not wanted:
        return None
    users = db.query(User).filter(User.is_active == "true").all()
    active = {str(user.id) for user in users}
    ids = {str(user.id) for user in users if _key(user.display_name) == wanted}
    ids.update(str(account.user_id) for account in db.query(TelegramAuthAccount).filter(
        TelegramAuthAccount.is_active == "true").all()
        if str(account.user_id) in active and _key(account.display_name) == wanted)
    return next(iter(ids)) if len(ids) == 1 else None


def authorize(db, user_id, chapter_id):
    user = db.get(User, str(user_id))
    if user is None or user.is_active != "true":
        raise ExportError("forbidden", 403)
    roles = {role for (role,) in db.query(UserRole.role).filter_by(user_id=user.id)}
    editor = bool(roles & {"admin", "author"})
    chapter = db.get(ScriptChapter, str(chapter_id))
    if chapter is None:
        raise ExportError("not_found", 404)
    if not editor:
        # Диктор не должен получить неопубликованный сценарий через прямой URL,
        # даже если прежде он был назначен на роль этой книги.
        if str(chapter.status or "") != "published":
            raise ExportError("not_found", 404)
        characters = db.query(Character).filter_by(book_id=chapter.book_id).all()
        identities = book_roles(db, chapter.book_id)
        names = [row.actor_name for row in characters if identities.is_narrator(row.name) and row.actor_name]
        if not names and not identities.typed:
            names = [row.actor_name for row in characters if row.narrator_role and row.actor_name]
        if not names:
            budget = db.query(BookBudget).filter_by(book_id=chapter.book_id).first()
            names = [budget.narrator_actor_name] if budget else []
        approved = [approved_actor(name) for name in names]
        if (not approved or any(not name for name in approved)
                or {_owner(db, name) for name in approved} != {str(user.id)}):
            raise ExportError("forbidden", 403)
    return chapter, editor


def _json_object(value):
    try:
        result = json.loads(value or "{}")
    except (ValueError, TypeError):
        return {}
    return result if isinstance(result, dict) else {}


def _bounds(anchor):
    if not isinstance(anchor, dict) or anchor.get("mapped") is False:
        return None
    try:
        start, end = float(anchor["start"]), float(anchor["end"])
    except (TypeError, ValueError, KeyError, OverflowError):
        return None
    return (start, end) if math.isfinite(start) and math.isfinite(end) and 0 <= start <= end else None


def current_export(db, user_id, chapter_id):
    from app.services.asr_audit import AsrAuditError, _narrator_snapshot
    from app.services.asr_audit_lifecycle import current_audit

    chapter, editor = authorize(db, user_id, chapter_id)
    try:
        audit = current_audit(db, chapter.id)
        if audit is None:
            return {"ok": True, "audit_id": None, "fingerprint": None, "items": [], "instructions": INSTRUCTIONS}, {}
        snapshot = _narrator_snapshot(db, chapter.id)
    except (AsrAuditError, ValueError, TypeError):
        return {"ok": True, "audit_id": None, "fingerprint": None, "items": [], "instructions": INSTRUCTIONS}, {}
    if _json_object(audit.lines_json).get("narrator_input_fingerprint") != snapshot["fingerprint"]:
        raise ExportError("audit_not_current")
    sources = {sid: source for sid, source in snapshot["sources"].items()
               if editor or _owner(db, source.get("actor_name")) == str(user_id)}
    lines = {line["index"]: line for line in
             snapshot["lines"]["required"] + snapshot["lines"]["tags"]}
    audio_rows = {sid: db.get(AudioFile, sid) for sid in sources}
    items = {}
    flags = _json_object(audit.findings_json).get("flags", [])
    for flag in flags if isinstance(flags, list) else []:
        if not isinstance(flag, dict) or flag.get("confirmed") is not True or type(flag.get("index")) is not int:
            continue
        line = lines.get(flag["index"])
        if (not line or str(flag.get("segment_id") or "") != line["segment_id"]
                or str(flag.get("text") or "").split() != str(line["text"]).split()):
            continue
        clips = [clip for clip in snapshot["clips"] if clip["index"] == flag["index"]
                 and clip["segment_id"] == line["segment_id"]
                 and ("role" not in flag or _key(flag["role"]) == _key(clip["role"]))
                 and ("role_index" not in flag or flag["role_index"] == clip["role_index"])]
        bound_ids = {str(window.get("source_audio_file_id") or "")
                     for window in flag.get("source_windows", []) if isinstance(window, dict)} \
            if isinstance(flag.get("source_windows", []), list) else set()
        anchors = flag.get("word_anchors")
        anchors = anchors if isinstance(anchors, list) and anchors else [flag.get("anchor")]
        bound_ids.update(str(anchor.get("source_audio_file_id") or "") for anchor in anchors if isinstance(anchor, dict))
        # Старый формат имеет только один исходник; ему всё равно нужен текущий клип.
        if not bound_ids:
            bound_ids.add(str(flag.get("source_audio_file_id") or flag.get("audio_file_id") or audit.audio_file_id or ""))
        for sid in sorted(bound_ids & sources.keys()):
            source_clips = [clip for clip in clips if clip["source_audio_file_id"] == sid]
            if not source_clips:
                continue
            markers = []
            for anchor in anchors:
                if not isinstance(anchor, dict):
                    continue
                anchor_sid = str(anchor.get("source_audio_file_id") or "")
                # Синтетическое время сборной сверки не имеет права стать меткой исходника.
                if not anchor_sid and (flag.get("word_anchors") or flag.get("source_windows")):
                    continue
                if (anchor_sid or str(flag.get("source_audio_file_id") or flag.get("audio_file_id") or audit.audio_file_id)) != sid:
                    continue
                bounds = _bounds(anchor)
                if bounds is None:
                    continue
                start, end = bounds
                duration = float(audio_rows[sid].duration_seconds or 0)
                if duration > 0 and end > duration:
                    continue
                if not any(clip["start"] <= start < clip["end"] and end <= clip["end"]
                           and ("segment_id" not in anchor or anchor["segment_id"] == clip["segment_id"])
                           and ("role" not in anchor or _key(anchor["role"]) == _key(clip["role"]))
                           and ("role_index" not in anchor or anchor["role_index"] == clip["role_index"])
                           for clip in source_clips):
                    continue
                markers.append({"start": start, "end": end, "approximate": True,
                                "word": str(anchor.get("word") or "")})
            missing = flag.get("missing_words")
            finding = {"index": flag["index"], "text": line["text"],
                       "missing_words": [str(word) for word in missing] if isinstance(missing, list) else [],
                       "note": str(flag.get("note") or ""), "confirm_note": str(flag.get("confirm_note") or ""),
                       "markers": markers, "marker_unavailable": not bool(markers)}
            item = items.setdefault(sid, {"audio_file_id": sid,
                "filename": str(audio_rows[sid].canonical_filename or audio_rows[sid].original_filename or "source.wav")
                            .replace("\\", "/").rsplit("/", 1)[-1], "findings": [],
                "download_url": f"/api/v2/chapters/{quote(str(chapter.id), safe='')}/narrator-audit/"
                                f"{quote(str(audit.id), safe='')}/sources/{quote(sid, safe='')}.zip",
                "labels_download_url": f"/api/v2/chapters/{quote(str(chapter.id), safe='')}/narrator-audit/"
                                       f"{quote(str(audit.id), safe='')}/sources/{quote(sid, safe='')}/labels.txt"})
            item["findings"].append(finding)
    return {"ok": True, "audit_id": audit.id, "fingerprint": snapshot["fingerprint"],
            "items": list(items.values()), "instructions": INSTRUCTIONS}, sources


def _plain(text):
    return " ".join(str(text or "").split())


def _open_source(source):
    if source.get("verify_state") in {"missing", "mismatch"}:
        raise ExportError("source_integrity_failed", 409)
    location = str(source.get("location") or "local")
    if location not in {"local", "nas"}:
        raise ExportError("source_unavailable", 503)
    if location == "nas":
        from app.services.nas_health import nas_online
        if nas_online() is not True:
            raise ExportError("source_unavailable", 503)
    key = str(source.get("stored_key") or "")
    parts = PurePosixPath(key).parts
    if not parts or key.startswith("/") or any(part in {".", ".."} for part in parts) or "\\" in key:
        raise ExportError("source_unavailable", 503)
    directory = None
    try:
        root = audio_storage.nas_root() if location == "nas" else audio_storage.local_root()
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        import stat
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ExportError("source_unavailable", 503)
        return os.fdopen(descriptor, "rb")
    except (OSError, RuntimeError, ValueError) as exc:
        raise ExportError("source_unavailable", 503) from exc
    finally:
        if directory is not None:
            os.close(directory)


# Один процесс uvicorn обслуживает всю студию: медленное скачивание тоже держит
# место на диске, поэтому разрешение живёт до закрытия потока, не до конца упаковки.
_export_slot = threading.BoundedSemaphore(1)


class _LimitedArchive:
    def __init__(self, target):
        self._target = target
        self._lock = threading.Lock()
        self._released = False

    def __getattr__(self, name):
        return getattr(self._target, name)

    def close(self):
        with self._lock:
            if not self._released:
                try:
                    self._target.close()
                finally:
                    self._released = True
                    _export_slot.release()


def _finding_label(finding):
    words = ", ".join(finding["missing_words"]) or "check text"
    return _plain(f"APPROXIMATE · {words} · Expected: {finding['text']} · "
                  f"{finding['note']} {finding['confirm_note']}")


def build_labels(item):
    """Метки зависят только от сохранённой сверки: исходный звук не нужен."""
    labels = [(marker["start"], marker["end"], _finding_label(finding))
              for finding in item["findings"] for marker in finding["markers"]]
    return "".join(f"{start:.6f}\t{end:.6f}\t{text}\n"
                   for start, end, text in sorted(labels)).encode("utf-8")


def _metadata(item, audit_id, fingerprint, digest):
    context = [_finding_label(finding) +
               (" · Time unavailable: find this passage by its text." if finding["marker_unavailable"] else "")
               for finding in item["findings"]]
    return {
        "labels.txt": build_labels(item),
        "findings.txt": ("\n\n".join(context) + "\n").encode("utf-8"),
        "README.txt": (INSTRUCTIONS + f"\n\nAudit: {audit_id}\nFingerprint: {fingerprint}\n"
            f"Source: {item['audio_file_id']}\nMD5 source.wav: {digest}\n"
            "source.wav is an exact byte-for-byte copy of the uploaded WAV without embedded labels.\n"
            "Labels refer to this original recording, not the chapter mix.\n"
            "Audacity: https://manual.audacityteam.org/man/importing_and_exporting_labels.html\n").encode("utf-8"),
    }


def build_archive(source, item, *, audit_id, fingerprint):
    """Временный архив собирается потоком и выдаётся только после сверки суммы."""
    expected = str(source.get("md5") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{32}", expected):
        raise ExportError("source_integrity_failed", 409)
    expected_size = int(source.get("size_bytes") or 0)
    if expected_size < 12:
        raise ExportError("source_integrity_failed", 409)
    if not _export_slot.acquire(blocking=False):
        raise ExportError("export_busy", 429)
    target = None
    try:
        metadata = _metadata(item, audit_id, fingerprint, expected)
        required = expected_size + sum(len(value) for value in metadata.values()) + 65536
        reserve = max(512 * 1024**2, int(settings.local_reserve_gb) * 1024**3)
        if shutil.disk_usage(tempfile.gettempdir()).free < required + reserve:
            raise ExportError("export_insufficient_space", 503)
        target = tempfile.TemporaryFile(mode="w+b")
        with _open_source(source) as original, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED,
                                                             allowZip64=True) as archive:
            if os.fstat(original.fileno()).st_size != int(source.get("size_bytes") or 0):
                raise ExportError("source_integrity_failed", 409)
            header = original.read(12)
            if len(header) != 12 or header[:4] not in {b"RIFF", b"RF64"} or header[8:12] != b"WAVE":
                raise ExportError("source_not_wav", 409)
            original.seek(0)
            digest, size = hashlib.md5(), 0
            with archive.open("source.wav", "w", force_zip64=True) as wav:
                while chunk := original.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    if size > expected_size:
                        raise ExportError("source_integrity_failed", 409)
                    wav.write(chunk)
            if (digest.hexdigest() != expected
                    or size != int(source.get("size_bytes") or 0)):
                raise ExportError("source_integrity_failed", 409)
            for name, content in metadata.items():
                archive.writestr(name, content)
        target.seek(0)
        return _LimitedArchive(target)
    except Exception as error:
        if target is not None:
            target.close()
        _export_slot.release()
        if isinstance(error, OSError):
            raise ExportError("export_unavailable", 503) from error
        raise


def archive_chunks(archive):
    try:
        while chunk := archive.read(1024 * 1024):
            yield chunk
    finally:
        archive.close()
