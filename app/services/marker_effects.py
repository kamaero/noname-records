"""Короткие эффекты привязаны к активному звуковому маркеру и его описанию."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import NAMESPACE_URL, uuid5
import wave

from sqlalchemy import select

from app.models import AudioFile, ScriptBook, ScriptChapter, SoundMarker, SoundtrackWork
from app.services import audio_storage
from app.services.audio_mirror import storage_key
from app.services.audition_session import SessionClip, SessionTrack

SOURCE = "marker_fx"


def candidate(marker):
    payload = marker.payload or {}
    inputs = [marker.book_id, marker.chapter_id, marker.id, marker.segment_id, marker.quote, payload]
    digest = hashlib.sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"marker": marker.id, "input_sha256": digest, "description": payload.get("description", ""),
            "queries": payload.get("queries", [])}


def plan(db, chapter_id):
    chapter = db.get(ScriptChapter, chapter_id)
    if chapter is None:
        raise ValueError("chapter_not_found")
    rows = db.scalars(select(SoundMarker).where(SoundMarker.book_id == chapter.book_id,
        SoundMarker.chapter_id == chapter.id, SoundMarker.kind == "sound", SoundMarker.status == "active")
        .order_by(SoundMarker.segment_id, SoundMarker.id))
    return {"chapter": chapter.id, "markers": [candidate(marker) for marker in rows]}


def records(db, chapter_id):
    rows = db.execute(select(SoundMarker, SoundtrackWork, AudioFile)
        .join(SoundtrackWork, (SoundtrackWork.identity == SoundMarker.id)
              & (SoundtrackWork.book_id == SoundMarker.book_id) & (SoundtrackWork.source == SOURCE))
        .join(AudioFile, AudioFile.id == SoundtrackWork.original["audio_id"].as_string())
        .where(SoundMarker.chapter_id == chapter_id, SoundMarker.kind == "sound",
               SoundMarker.status == "active", AudioFile.kind == "sound_effect")
        .order_by(SoundMarker.segment_id, SoundMarker.id))
    return [(marker, audio) for marker, work, audio in rows
            if work.original.get("input_sha256") == candidate(marker)["input_sha256"]]


def files(db, chapter_id):
    return [audio for _, audio in records(db, chapter_id)]


def write_zip_member(archive, item):
    """Проверяем те же байты, что отдаём монтажёру, до отметки о сдаче главы."""
    from app.services.chapter_delivery import StorageUnavailable
    digest = hashlib.md5()
    size = 0
    path = audio_storage.resolve_path(item.stored_key, location=item.location)
    with open(path, "rb") as stream, archive.open(item.canonical_filename, "w", force_zip64=True) as target:
        while block := stream.read(1024 * 1024):
            target.write(block)
            digest.update(block)
            size += len(block)
    if size != item.size_bytes or digest.hexdigest() != item.md5:
        raise StorageUnavailable("fx_integrity_failed")


def session_track(db, chapter_id, starts, *, relative):
    from app.services.chapter_delivery import _placed_markers
    track = SessionTrack(name="FX", channel_type="stereo")
    rows = [{"segment_id": marker.segment_id, "audio": audio} for marker, audio in records(db, chapter_id)]
    placed, _ = _placed_markers(rows, starts) if rows else ([], 0)
    for row, start, _shifted in placed:
        item = row["audio"]
        track.clips.append(SessionClip(name=item.canonical_filename, start=start,
            source_out=item.duration_seconds, path=audio_storage.resolve_path(item.stored_key),
            relative=item.canonical_filename if relative else ""))
    return track


def register(db, chapter_id, entry, data, *, apply=False):
    """Вызывающий держит транзакцию одного маркера. Файл публикуется до ссылки на него."""
    from app.services.chapter_delivery import book_token, derive_book_code, mark_session_outdated

    marker = db.get(SoundMarker, entry["marker"])
    chapter = db.get(ScriptChapter, chapter_id)
    if (marker is None or chapter is None or marker.book_id != chapter.book_id
            or marker.chapter_id != chapter_id or marker.kind != "sound" or marker.status != "active"
            or candidate(marker)["input_sha256"] != entry["input_sha256"]):
        raise ValueError("marker_changed")
    recipe = entry["recipe"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", recipe):
        raise ValueError("invalid_recipe")
    sha = hashlib.sha256(data).hexdigest()
    if sha != entry["sha256"]:
        raise ValueError("wav_checksum_mismatch")
    with wave.open(io.BytesIO(data)) as wav:
        rate, channels, frames, width = wav.getframerate(), wav.getnchannels(), wav.getnframes(), wav.getsampwidth()
        if (rate <= 0 or channels not in (1, 2) or not frames or width not in (1, 2, 3, 4)
                or len(wav.readframes(frames)) != frames * channels * width):
            raise ValueError("invalid_wav")
    credits = entry["credits"]
    if not isinstance(credits, list) or any(not isinstance(c, dict) or not c.get("license") for c in credits):
        raise ValueError("invalid_credits")
    identity = str(uuid5(NAMESPACE_URL, f"marker-fx/{marker.id}/{recipe}/{sha}"))
    book = db.get(ScriptBook, chapter.book_id)
    code = book_token(derive_book_code(book.title))
    name = f"FX-{recipe}-{identity}.wav"
    key = storage_key(code, str(chapter.chapter_index), name, "sound_effect")
    path = Path(audio_storage.resolve_path(key))
    work = db.scalar(select(SoundtrackWork).filter_by(book_id=book.id, source=SOURCE, identity=marker.id))
    original = {"audio_id": identity, "recipe": recipe, "generator": entry["generator"],
                "credits": credits, "input_sha256": entry["input_sha256"], "sha256": sha}
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise ValueError("stored_audio_changed")
    if work and work.original == original and db.get(AudioFile, identity) and path.is_file():
        return "unchanged"
    if not apply:
        return "ready"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            os.link(tmp.name, path)
    if db.get(AudioFile, identity) is None:
        scope = {"book_id": book.id, "chapter_id": chapter.id} if hasattr(AudioFile, "chapter_id") else {}
        db.add(AudioFile(id=identity, **scope, book_code=code, chapter=str(chapter.chapter_index),
            kind="sound_effect", role="FX", original_filename=name, canonical_filename=name,
            stored_key=key, location="local", mime_type="audio/wav", size_bytes=len(data),
            md5=hashlib.md5(data).hexdigest(), duration_seconds=frames / rate,
            sample_rate=rate, channels=channels, codec="pcm"))
    if work is None:
        work = SoundtrackWork(book_id=book.id, source=SOURCE, identity=marker.id)
        db.add(work)
    work.original = original
    mark_session_outdated(db, chapter.id)
    return "registered"
