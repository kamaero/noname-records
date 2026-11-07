"""Черновик главы и утверждённые кредиты живут отдельно от архивов и аудиофайлов."""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from collections import OrderedDict

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert

from app.models import (AmbientTrack, AudioFile, ScriptBook, ScriptChapter, SoundMarker,
                        SoundtrackEntry, SoundtrackRejection, SoundtrackRequest,
                        SoundtrackSnapshot, SoundtrackState, SoundtrackWork, YoutubeSoundtrackDownload)
from app.services.openverse_client import audio_id, public_url
from app.time_utils import iso_utc

LAYERS = {"music", "ambience", "fx"}
METADATA = {"title": 300, "author": 300, "source_url": 2048, "license": 100,
            "license_version": 30, "license_url": 2048, "attribution": 10000}


class SoundtrackError(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def chapter_for(db, chapter_id):
    chapter = db.get(ScriptChapter, chapter_id)
    if chapter is None or db.get(ScriptBook, chapter.book_id) is None:
        raise SoundtrackError("not_found", 404)
    return chapter


def _scene_label(db, marker):
    from app.services.sound_store import _live_place

    markers = db.scalars(select(SoundMarker).where(SoundMarker.chapter_id == marker.chapter_id,
        SoundMarker.kind == "scene", SoundMarker.status == "active").order_by(SoundMarker.segment_id, SoundMarker.id)).all()
    ordinal = next((index for index, row in enumerate(markers, 1) if row.id == marker.id), 0)
    place = _live_place(db, marker.book_id, marker.place_id) if marker.place_id else None
    return (f"Scene {ordinal}" + (f" · {place.name}" if place is not None and place.name else ""))[:300]


def _scene(db, chapter, scene_id):
    if scene_id is None or scene_id == "":
        return "", ""
    if not isinstance(scene_id, str) or len(scene_id) > 36:
        raise SoundtrackError("bad_scene")
    marker = db.get(SoundMarker, scene_id)
    if marker is None or marker.chapter_id != chapter.id or marker.book_id != chapter.book_id or marker.kind != "scene" or marker.status != "active":
        raise SoundtrackError("not_found", 404)
    return marker.id, _scene_label(db, marker)


def _metadata(body, *, title_required=False):
    result = {}
    for key, limit in METADATA.items():
        if key not in body:
            continue
        value = body[key]
        if not isinstance(value, str) or len(value) > limit or any(ord(char) < 32 and char not in "\n\t" for char in value):
            raise SoundtrackError("bad_" + key)
        value = value.strip()
        if key in {"source_url", "license_url"} and value and not public_url(value):
            raise SoundtrackError("bad_" + key)
        result[key] = value
    if title_required and not result.get("title"):
        raise SoundtrackError("bad_title")
    return result


def _legacy(db, chapter):
    book = db.get(ScriptBook, chapter.book_id)
    rows = db.execute(select(AmbientTrack, AudioFile).join(AudioFile, AmbientTrack.audio_file_id == AudioFile.id)
        .where(AmbientTrack.chapter_id == chapter.id, AmbientTrack.book_id == chapter.book_id,
               AmbientTrack.language_snapshot == book.language,
               AmbientTrack.status.in_(("done", "replaced")))).all()
    return [{"ambient_track_id": track.id, "audio_file_id": audio.id,
             "title": audio.canonical_filename or audio.original_filename,
             "status": track.status, "preview_url": f"/api/v2/ambient/{audio.id}/audio"}
            for track, audio in rows]


def _entry_view(db, row):
    work = db.get(SoundtrackWork, row.work_id)
    original = copy.deepcopy(work.original)
    metadata = {**original, **copy.deepcopy(row.overrides)}
    # Исправление текста лицензии не превращается в автоматическую проверку прав.
    if any(k in row.overrides for k in ("license", "license_version", "license_url")):
        metadata.update(rights_status="unknown", rights_warning="Metadata was edited manually; check the source terms.")
    marker = db.get(SoundMarker, row.scene_id) if row.scene_id else None
    missing = bool(row.scene_id) and (marker is None or marker.status != "active" or marker.chapter_id != row.chapter_id or marker.book_id != row.book_id)
    return {**metadata, "id": row.id, "work_id": work.id, "source": work.source,
            "scene_id": row.scene_id or None, "scene_title": _scene_label(db, marker) if marker and not missing else row.scene_title,
            "scene_missing": missing, "layer": row.layer, "used": row.used,
            "original": original, "overrides": copy.deepcopy(row.overrides)}


def chapter_payload(db, chapter_id):
    chapter = chapter_for(db, chapter_id)
    state = db.get(SoundtrackState, chapter.id)
    entries = db.scalars(select(SoundtrackEntry).where(SoundtrackEntry.chapter_id == chapter.id,
                                                      SoundtrackEntry.removed.is_(False)).order_by(SoundtrackEntry.id)).all()
    rejections = db.scalars(select(SoundtrackRejection).where(SoundtrackRejection.chapter_id == chapter.id)).all()
    snapshots = db.scalars(select(SoundtrackSnapshot).where(SoundtrackSnapshot.chapter_id == chapter.id)
                           .order_by(SoundtrackSnapshot.version.desc())).all()
    return {"ok": True, "chapter_id": chapter.id, "book_id": chapter.book_id,
            "version": state.version if state else 0, "locked": bool(state and state.locked),
            "snapshot_id": state.snapshot_id if state else None,
            "entries": [_entry_view(db, entry) for entry in entries],
            "rejections": [{"scene_id": r.scene_id or None, "layer": r.layer, "openverse_id": r.openverse_id} for r in rejections],
            "snapshots": [{"id": s.id, "version": s.version, "created_at": iso_utc(s.created_at),
                           "entries": copy.deepcopy(s.entries)} for s in snapshots],
            "legacy_tracks": _legacy(db, chapter)}


def request_fingerprint(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _replay(db, chapter_id, body):
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 80:
        raise SoundtrackError("bad_request_id")
    row = db.scalar(select(SoundtrackRequest).where(SoundtrackRequest.chapter_id == chapter_id,
                                                   SoundtrackRequest.request_id == request_id))
    if row is not None:
        if row.fingerprint != request_fingerprint(body):
            raise SoundtrackError("request_conflict", 409)
        return copy.deepcopy(row.response)
    return None


def mutate(db, chapter_id, body, *, openverse_detail=None):
    if not isinstance(body, dict):
        raise SoundtrackError("bad_request")
    chapter = chapter_for(db, chapter_id)
    replay = _replay(db, chapter.id, body)
    if replay is not None:
        return replay
    version = body.get("version")
    action = body.get("action")
    if type(version) is not int or version < 0:
        raise SoundtrackError("bad_version")
    if not isinstance(action, str) or action not in {"select", "update", "remove", "reject", "restore", "finalize", "unlock"}:
        raise SoundtrackError("bad_action")
    # Внешний каталог может отвечать секунды. До него не берём write-lock SQLite,
    # иначе подбор одного трека остановит загрузки и правки всего сайта.
    prepared_openverse = None
    if action == "select" and body.get("source") == "openverse":
        identity = audio_id(body.get("openverse_id"))
        existing = db.scalar(select(SoundtrackWork).where(SoundtrackWork.book_id == chapter.book_id,
            SoundtrackWork.source == "openverse", SoundtrackWork.identity == identity))
        if existing is not None:
            prepared_openverse = copy.deepcopy(existing.original)
        elif openverse_detail is not None:
            prepared_openverse = openverse_detail(identity)
        else:
            raise SoundtrackError("openverse_unavailable", 503)
    # Первой записью берём блокировку SQLite: все дальнейшие проверки и снимок —
    # одна транзакция, конкурентный закрепитель не увидит половину выбора.
    db.execute(insert(SoundtrackState).values(chapter_id=chapter.id, book_id=chapter.book_id,
        version=0, locked=False).on_conflict_do_nothing(index_elements=["chapter_id"]))
    db.expire_all()
    chapter = chapter_for(db, chapter_id)
    replay = _replay(db, chapter.id, body)
    if replay is not None:
        return replay
    condition = SoundtrackState.locked.is_(True) if action == "unlock" else SoundtrackState.locked.is_(False)
    changed = db.execute(update(SoundtrackState).where(SoundtrackState.chapter_id == chapter.id,
        SoundtrackState.version == version, condition).values(version=version + 1).execution_options(synchronize_session=False)).rowcount
    if changed != 1:
        raise SoundtrackError("version_conflict", 409)
    db.expire_all()
    state = db.get(SoundtrackState, chapter.id)
    if action in {"select", "reject", "restore"}:
        layer = body.get("layer", "music")
        if not isinstance(layer, str) or layer not in LAYERS:
            raise SoundtrackError("bad_layer")
        scene_id, scene_title = _scene(db, chapter, body.get("scene_id"))
    if action == "select":
        source = body.get("source")
        if not isinstance(source, str) or source not in {"openverse", "collection", "elevenlabs"}:
            raise SoundtrackError("bad_source")
        metadata = _metadata(body, title_required=source != "openverse" and not (source == "elevenlabs" and body.get("ambient_track_id")))
        if "used" in body and type(body["used"]) is not bool:
            raise SoundtrackError("bad_used")
        if source == "openverse":
            identity = audio_id(body.get("openverse_id"))
            work = db.scalar(select(SoundtrackWork).where(SoundtrackWork.book_id == chapter.book_id,
                SoundtrackWork.source == source, SoundtrackWork.identity == identity))
            if work is None:
                original = prepared_openverse
            else:
                original = work.original
        elif source == "elevenlabs" and body.get("ambient_track_id"):
            if not isinstance(body["ambient_track_id"], str) or len(body["ambient_track_id"]) > 36:
                raise SoundtrackError("bad_ambient_track_id")
            track = db.get(AmbientTrack, body["ambient_track_id"])
            if track is None or track.book_id != chapter.book_id or track.chapter_id != chapter.id or track.status not in {"done", "replaced"} or track.language_snapshot != db.get(ScriptBook, chapter.book_id).language:
                raise SoundtrackError("not_found", 404)
            audio = db.get(AudioFile, track.audio_file_id) if track.audio_file_id else None
            if audio is None:
                raise SoundtrackError("not_found", 404)
            identity = "ambient:" + track.id
            original = {"title": audio.canonical_filename or audio.original_filename, "ambient_track_id": track.id,
                        "audio_file_id": audio.id, "duration_seconds": track.duration_seconds,
                        "preview_url": f"/api/v2/ambient/{audio.id}/audio", "download_url": ""}
            work = db.scalar(select(SoundtrackWork).where(SoundtrackWork.book_id == chapter.book_id,
                SoundtrackWork.source == source, SoundtrackWork.identity == identity))
        else:
            identity = str(uuid.uuid4())
            original, work = {}, None
        if source != "openverse":
            original = {**original, "rights_status": "unknown", "rights_warning": "Rights have not been verified; review the terms yourself."}
        if work is None:
            work = SoundtrackWork(book_id=chapter.book_id, source=source, identity=identity, original=original)
            db.add(work)
            db.flush()
        entry = db.scalar(select(SoundtrackEntry).where(SoundtrackEntry.chapter_id == chapter.id,
            SoundtrackEntry.work_id == work.id, SoundtrackEntry.scene_id == scene_id, SoundtrackEntry.layer == layer))
        if entry is None:
            entry = SoundtrackEntry(book_id=chapter.book_id, chapter_id=chapter.id, work_id=work.id,
                scene_id=scene_id, scene_title=scene_title, layer=layer)
            db.add(entry)
        entry.removed = False
        entry.used = body.get("used", bool(entry.used))
        entry.overrides = {**(entry.overrides or {}), **metadata}
    elif action in {"update", "remove"}:
        identity = body.get("entry_id")
        if not isinstance(identity, str):
            raise SoundtrackError("bad_entry_id")
        entry = db.get(SoundtrackEntry, identity)
        if entry is None or entry.chapter_id != chapter.id or entry.book_id != chapter.book_id or entry.removed:
            raise SoundtrackError("not_found", 404)
        if action == "remove":
            entry.removed, entry.used = True, False
        else:
            metadata = _metadata(body)
            if "title" in metadata and not metadata["title"]:
                raise SoundtrackError("bad_title")
            entry.overrides = {**entry.overrides, **metadata}
            if "scene_id" in body:
                entry.scene_id, entry.scene_title = _scene(db, chapter, body["scene_id"])
            if "layer" in body:
                if not isinstance(body["layer"], str) or body["layer"] not in LAYERS:
                    raise SoundtrackError("bad_layer")
                entry.layer = body["layer"]
            if "used" in body:
                if type(body["used"]) is not bool:
                    raise SoundtrackError("bad_used")
                entry.used = body["used"]
    elif action in {"reject", "restore"}:
        identity = audio_id(body.get("openverse_id"))
        row = db.scalar(select(SoundtrackRejection).where(SoundtrackRejection.chapter_id == chapter.id,
            SoundtrackRejection.scene_id == scene_id, SoundtrackRejection.layer == layer,
            SoundtrackRejection.openverse_id == identity))
        if action == "reject" and row is None:
            db.add(SoundtrackRejection(book_id=chapter.book_id, chapter_id=chapter.id,
                scene_id=scene_id, layer=layer, openverse_id=identity))
        if action == "restore" and row is not None:
            db.delete(row)
    elif action == "finalize":
        pending_download = db.scalar(select(YoutubeSoundtrackDownload.id).where(
            YoutubeSoundtrackDownload.chapter_id == chapter.id,
            YoutubeSoundtrackDownload.status.in_(("queued", "running"))).limit(1))
        if pending_download:
            raise SoundtrackError("youtube_download_pending", 409)
        db.flush()
        entries = [entry for entry in chapter_payload(db, chapter.id)["entries"] if entry["used"]]
        if not entries and body.get("confirm_empty") is not True:
            raise SoundtrackError("confirm_empty_required")
        snapshot = SoundtrackSnapshot(book_id=chapter.book_id, chapter_id=chapter.id,
            version=version + 1, entries=copy.deepcopy(entries))
        db.add(snapshot)
        db.flush()
        state.locked, state.snapshot_id = True, snapshot.id
    else:
        state.locked = False
    db.flush()
    response = chapter_payload(db, chapter.id)
    db.add(SoundtrackRequest(book_id=chapter.book_id, chapter_id=chapter.id,
        request_id=body["request_id"], fingerprint=request_fingerprint(body), response=copy.deepcopy(response)))
    db.flush()
    return response


def credits_text(db, chapter_id):
    chapter = chapter_for(db, chapter_id)
    state = db.get(SoundtrackState, chapter.id)
    if state is None or not state.locked or not state.snapshot_id:
        raise SoundtrackError("not_finalized", 409)
    snapshot = db.get(SoundtrackSnapshot, state.snapshot_id)
    works = OrderedDict()
    for entry in snapshot.entries:
        works.setdefault(entry["work_id"], []).append(entry)
    output = [f"Soundtrack: {chapter.chapter_title or chapter.chapter_index}",
              f"Finalized version: {snapshot.version}",
              "Verify attribution and original source terms. Missing information does not establish rights.", ""]
    if not works:
        output.append("No recordings used (confirmed by the editor).")
    for entries in works.values():
        entry = entries[0]
        for label, key in (("Source", "source"), ("Title", "title"), ("Author", "author"),
                           ("URL", "source_url"), ("License", "license"), ("License version", "license_version"),
                           ("License URL", "license_url"), ("Attribution", "attribution")):
            values = list(dict.fromkeys(str(e.get(key) or "not listed") for e in entries))
            output.append(f"{label}: {' / '.join(values)}")
        output.append("Scenes: " + "; ".join(dict.fromkeys(
            (e.get("scene_title") or "Whole chapter") +
            (f" [{e['scene_id']}]" if e.get("scene_id") else "") +
            (" (scene no longer exists)" if e.get("scene_missing") else "") for e in entries)))
        output.append("")
    return "\n".join(output) + "\n"
