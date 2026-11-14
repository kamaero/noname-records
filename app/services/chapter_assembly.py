"""Рабочая сборка начинается с рассказчика; новые версии не заменяют ручной монтаж."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import errno
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree as ET
from defusedxml.ElementTree import fromstring as safe_fromstring

from sqlalchemy import or_

from app.models import AsrJob, AudioFile, BotUpload, ScriptBook, ScriptChapter
from app.services import audio_storage, nas_health
from app.time_utils import utcnow_naive


def narrator_ready_reason(db, chapter):
    """Успешная старая сверка не разрешает собирать ещё принимаемый новый дубль."""
    from app.services.asr_audit import latest_narrator_take
    from app.services.role_identity import chapter_roles

    identities = chapter_roles(db, chapter.id)
    take = latest_narrator_take(db, chapter)
    if take is None:
        return "missing_narrator_recording"
    active = db.query(BotUpload).filter(BotUpload.book_id == chapter.book_id,
        BotUpload.purpose == "take", BotUpload.state.in_(["choose", "queued", "processing"])).all()
    if any(identities.is_narrator(row.role_name) for row in active):
        return "narrator_intake_pending"
    job = db.query(AsrJob).filter(AsrJob.audio_file_id == take.id,
        or_(AsrJob.chapter_id == chapter.id, AsrJob.chapter_id.is_(None), AsrJob.chapter_id == ""))\
        .order_by(AsrJob.created_at.desc(), AsrJob.id.desc()).first()
    if job is None or job.status != "done":
        return "narrator_asr_pending" if job is None or job.status in {"queued", "running"} else "narrator_asr_failed"
    return ""


@contextmanager
def _directory(root, parts):
    # Корень NAS не создаём: отсутствие точки монтирования должно остаться ошибкой.
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in parts:
            if name in {"", ".", ".."} or "/" in name or "\\" in name:
                raise ValueError("unsafe_assembly_directory")
            try:
                os.mkdir(name, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _digest(handle):
    digest, size = hashlib.md5(), 0
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _publish(directory, name, write):
    """Готовое имя появляется атомарно, чужой файл не заменяется даже при гонке."""
    pending = ".assembly-" + uuid4().hex + ".partial"
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, "wb") as target:
            write(target)
            target.flush()
            os.fsync(target.fileno())
        os.link(pending, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.fsync(directory)
    finally:
        os.unlink(pending, dir_fd=directory)


def _media(directory, item):
    path = audio_storage.resolve_path(item.stored_key, location=item.location or "local")
    with open(path, "rb") as source:
        before = os.fstat(source.fileno())
        digest, size = _digest(source)
        after = os.fstat(source.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("assembly_source_changed")
        if size <= 0 or (item.size_bytes and size != item.size_bytes) or (item.md5 and digest != item.md5):
            raise ValueError("assembly_source_integrity_failed")
        suffix = Path(item.canonical_filename or item.stored_key).suffix.lower()
        suffix = suffix if suffix in {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aiff", ".aif"} else ".audio"
        name = hashlib.sha256((str(item.id) + ":" + digest).encode()).hexdigest()[:32] + suffix
        try:
            cached = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            needs_copy = True
        except OSError as exc:
            if exc.errno != errno.ELOOP:
                raise
            name = Path(name).stem + "_" + uuid4().hex[:12] + suffix
            needs_copy = True
        else:
            with os.fdopen(cached, "rb") as existing:
                needs_copy = _digest(existing) != (digest, size)
            if needs_copy:
                # Ручной WAV не заменяем и не перемещаем: новая версия получает
                # отдельное имя для исходных байтов, прежний монтаж остаётся цел.
                name = Path(name).stem + "_" + uuid4().hex[:12] + suffix
        if needs_copy:
            source.seek(0)
            def copy(target):
                copied = hashlib.md5()
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    copied.update(chunk)
                if copied.hexdigest() != digest:
                    raise ValueError("assembly_source_changed")
            _publish(directory, name, copy)
    return name, digest, size


def _inputs(db, chapter_id):
    from app.services import chapter_delivery as delivery
    chapter = db.get(ScriptChapter, str(chapter_id))
    if chapter is None or chapter.status != "published":
        return None, "chapter_not_published"
    reason = narrator_ready_reason(db, chapter)
    if reason:
        return None, reason
    layout = delivery._script_layout(db, chapter.id, relative=True)
    xml, skipped = delivery.build_chapter_session_with_stats(db, chapter.id, relative=True,
        include_unplaced=True, include_missing=True)
    if not xml:
        return None, "empty_session"
    items = [item for rows in layout[2].values() for item in rows]
    items += delivery.chapter_ambient_files(db, chapter.id)
    by_path = {os.path.abspath(audio_storage.resolve_path(item.stored_key, location=item.location or "local")): item
               for item in items}
    document = safe_fromstring(xml.lstrip("\ufeff"))
    sources = []
    for entry in document.findall("./files/file"):
        item = by_path.get(os.path.abspath(entry.attrib["absolutePath"]))
        if item is None:
            raise ValueError("unknown_assembly_source")
        if item.verify_state in {"missing", "mismatch"}:
            raise ValueError("assembly_source_integrity_failed")
        sources.append(item)
    signature = {"session": delivery.session_fingerprint(xml), "sources": [
        [str(item.id), item.stored_key, item.location, item.md5, item.size_bytes] for item in sources]}
    digest = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    return (chapter, xml, sources, digest, skipped), ""


def assemble_narrator_chapter(db, chapter_id):
    """Сначала реальные файлы, затем реестр и только после commit — платная очередь."""
    from app.services import chapter_delivery as delivery
    from app.services.asr_audit_lifecycle import record_audit_assembly

    state, reason = _inputs(db, chapter_id)
    if state is None:
        return {"written": False, "reason": reason, "replaced": ""}
    chapter, xml, sources, digest, skipped = state
    if any(item.location == "nas" for item in sources) and nas_health.nas_online() is not True:
        return {"written": False, "reason": "nas_offline", "replaced": ""}
    location = "nas" if audio_storage.nas_root() else "local"
    if location == "nas":
        if nas_health.nas_online() is not True:
            return {"written": False, "reason": "nas_offline", "replaced": ""}
        from app.services.audio_mirror import roots_overlap
        if roots_overlap():
            return {"written": False, "reason": "roots_overlap", "replaced": ""}
    root = audio_storage.nas_root() if location == "nas" else audio_storage.local_root()
    book = db.get(ScriptBook, chapter.book_id)
    code = delivery.book_token(delivery.derive_book_code(book.title)) or "BOOK"
    from app.services.audio_mirror import chapter_folder
    parts = (code, chapter_folder(delivery._chapter_label(chapter)))
    previous = str(chapter.session_key or "")
    # Копирование не держит SQLite writer; повторная сверка входа происходит ниже.
    db.commit()
    with _directory(root, parts) as folder:
        lock = os.open(".assembly.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                       0o600, dir_fd=folder)
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {"written": False, "reason": "assembly_busy", "replaced": ""}
            if (chapter.session_input_sha256 == digest and previous and chapter.session_location == location
                    and audio_storage.file_exists(previous, location=location)):
                record_audit_assembly(db, chapter.id, narrator_only=True)
                chapter.session_outdated_at = None
                return {"written": False, "reason": "unchanged", "replaced": "", "session_key": previous}
            mapping = {}
            with _directory(root, (*parts, "assembly-media")) as media:
                for item in sources:
                    name, _checksum, _size = _media(media, item)
                    path = os.path.abspath(audio_storage.resolve_path(item.stored_key, location=item.location or "local"))
                    mapping[path] = "assembly-media/" + name
            # Полная сериализация XML превращает CDATA пресетов эффектов в текст.
            # Проверяем документ безопасным парсером, переписываем только file-теги.
            def rewrite_file(match):
                entry = safe_fromstring(match.group(0))
                relative = mapping[os.path.abspath(entry.attrib["absolutePath"])]
                entry.set("absolutePath", os.path.join(root, *parts, relative))
                entry.set("relativePath", relative)
                return ET.tostring(entry, encoding="unicode")
            xml = re.sub(r"<file\s[^>]*?/>", rewrite_file, xml)
            data = xml.encode("utf-8")
            db.rollback()
            latest, reason = _inputs(db, chapter_id)
            if latest is None or latest[3] != digest:
                return {"written": False, "reason": "assembly_input_changed", "replaced": ""}
            chapter = latest[0]
            name = f"{code}_Ch{chapter.chapter_index:02d}_{utcnow_naive():%Y%m%dT%H%M%S}_{uuid4().hex[:10]}.sesx"
            _publish(folder, name, lambda target: target.write(data))
            record_audit_assembly(db, chapter.id, narrator_only=True)
            chapter.session_key = "/".join((*parts, name))
            chapter.session_location = location
            chapter.session_input_sha256 = digest
            chapter.session_sha256 = delivery.session_fingerprint(xml)
            chapter.session_archived_at = utcnow_naive()
            chapter.session_outdated_at = None
            chapter.session_markers_skipped = skipped
            db.add(chapter)
            return {"written": True, "reason": "", "replaced": previous,
                    "session_key": chapter.session_key, "session_location": location}
        finally:
            os.close(lock)


def archive_pending_chapters(db):
    """Зеркальный цикл использует новый допуск; старый ручной экспорт совместим."""
    from app.services.chapter_delivery import archive_pending_chapter_sessions
    return archive_pending_chapter_sessions(db, narrator_only=True)
