"""Переносимые пары готовых WAV: без провайдера, его ключей и удалённого генератора."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import uuid
import wave

from sqlalchemy import inspect

from app.models import AmbientTrack, AudioFile, ScriptBook, ScriptChapter, SoundMarker, SoundPlace
from app.services import audio_storage


def schema_ready(db):
    return {"variant", "pair_key", "scene_fingerprint"} <= {column["name"] for column in inspect(db.connection()).get_columns("ambient_tracks")}


def scene_fingerprint(db, marker):
    book = db.get(ScriptBook, marker.book_id)
    place = db.get(SoundPlace, marker.place_id) if marker.place_id else None
    payload = [marker.id, marker.chapter_id, marker.segment_id, marker.place_id,
               [place.name, place.description] if place else None,
               marker.payload, marker.text_sha256, book.language if book else ""]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def inspect_wav(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError("WAV source does not exist")
    with wave.open(str(path), "rb") as audio:
        frames, rate, channels, width = audio.getnframes(), audio.getframerate(), audio.getnchannels(), audio.getsampwidth()
        if rate <= 0 or frames <= 0 or channels not in (1, 2) or width != 2 or audio.getcomptype() != "NONE":
            raise ValueError("Expected a nonempty mono/stereo PCM16 WAV")
        read = 0
        while chunk := audio.readframes(65536):
            read += len(chunk)
        if read != frames * channels * width:
            raise ValueError("Truncated WAV")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"duration_seconds": frames / rate, "sample_rate": rate, "channels": channels,
            "size_bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def current_pairs(db, chapter_id):
    if not schema_ready(db):
        return {}
    markers = {row.id: row for row in db.query(SoundMarker).filter(
        SoundMarker.chapter_id == chapter_id, SoundMarker.kind == "scene", SoundMarker.status == "active")}
    rows = db.query(AmbientTrack, AudioFile).join(AudioFile, AudioFile.id == AmbientTrack.audio_file_id).filter(
        AmbientTrack.chapter_id == chapter_id, AmbientTrack.status == "done",
        AmbientTrack.variant.in_(("A", "B")), AudioFile.kind == "ambient").order_by(AmbientTrack.created_at, AmbientTrack.id).all()
    groups = {}
    for track, audio in rows:
        marker = markers.get(track.marker_id)
        if marker is None or track.scene_fingerprint != scene_fingerprint(db, marker):
            continue
        duration = float(audio.duration_seconds or 0)
        if not math.isfinite(duration) or duration <= 0:
            continue
        groups.setdefault((track.marker_id, track.pair_key), {})[track.variant] = audio
    result = {}
    for (marker_id, _key), pair in groups.items():
        if set(pair) == {"A", "B"}:
            result[marker_id] = pair
    return result


def import_pair(db, *, chapter_id, marker_id, variant_a, variant_b, apply=False):
    """Пара принимается одной транзакцией; файлы прежних вариантов не удаляем.

    Вызывающий делает commit. Без apply проверяются оба исходника, но хранилище
    и база не меняются. Блокировку CLI берёт до проверки идемпотентности.
    """
    if not schema_ready(db):
        raise ValueError("Upgrade the database to migration 0071 before importing pairs")
    chapter = db.get(ScriptChapter, chapter_id)
    marker = db.get(SoundMarker, marker_id)
    if chapter is None or marker is None or marker.chapter_id != chapter_id or marker.book_id != chapter.book_id or marker.kind != "scene" or marker.status != "active":
        raise ValueError("Expected an active scene belonging to this chapter")
    sources = {"A": Path(variant_a), "B": Path(variant_b)}
    metadata = {key: inspect_wav(path) for key, path in sources.items()}
    snapshot = scene_fingerprint(db, marker)
    key = hashlib.sha256(json.dumps([snapshot, metadata["A"]["sha256"], metadata["B"]["sha256"]]).encode()).hexdigest()
    existing = db.query(AmbientTrack).filter(AmbientTrack.marker_id == marker_id, AmbientTrack.pair_key == key,
        AmbientTrack.variant.in_(("A", "B")), AmbientTrack.status == "done").all()
    report = {"chapter_id": chapter_id, "marker_id": marker_id, "pair_key": key,
              "variants": metadata, "applied": False, "existing": len(existing) == 2}
    if report["existing"]:
        for row in existing:
            audio = db.get(AudioFile, row.audio_file_id)
            try:
                if audio is None or inspect_wav(audio_storage.resolve_path(audio.stored_key, location=audio.location)) != metadata[row.variant]:
                    report["existing"] = False
            except (OSError, ValueError, EOFError, wave.Error):
                report["existing"] = False
    if report["existing"] or not apply:
        return report
    from app.services.ambient_engine import _ensure_room
    from app.services.audio_mirror import chapter_folder
    from app.services.audio_naming import book_token
    from app.services.audio_uploads import derive_book_code
    from app.services.chapter_delivery import _chapter_label
    book = db.get(ScriptBook, chapter.book_id)
    code, label = derive_book_code(book.title), _chapter_label(chapter)
    _ensure_room(sum(item["size_bytes"] for item in metadata.values()))
    folder = f"{book_token(code) or 'BOOK'}/{chapter_folder(label)}"
    written = []
    try:
        new_rows = []
        for variant, source in sources.items():
            info = metadata[variant]
            name = f"Ambient_{uuid.uuid4().hex}_{variant}.wav"
            stored_key = f"{folder}/{name}"
            written.append(stored_key)
            with source.open("rb") as handle:
                size, md5 = audio_storage.write_stream(stored_key, handle)
            stored = inspect_wav(audio_storage.resolve_path(stored_key))
            if stored != info:
                raise ValueError("Source WAV changed during import")
            audio = AudioFile(book_code=code, chapter=label, original_filename=name, canonical_filename=name,
                stored_key=stored_key, mime_type="audio/wav", size_bytes=size, md5=md5, kind="ambient", role="Ambient",
                status="uploaded", location="local", duration_seconds=info["duration_seconds"],
                sample_rate=info["sample_rate"], channels=info["channels"], codec="pcm_s16le")
            db.add(audio)
            db.flush()
            new_rows.append(AmbientTrack(book_id=chapter.book_id, chapter_id=chapter_id, marker_id=marker_id,
                audio_file_id=audio.id, status="done", variant=variant, pair_key=key,
                scene_fingerprint=snapshot, language_snapshot=book.language,
                summary=f"Imported alternative {variant}", duration_seconds=math.ceil(info["duration_seconds"])))
        db.query(AmbientTrack).filter(AmbientTrack.marker_id == marker_id,
            AmbientTrack.variant.in_(("A", "B")), AmbientTrack.status == "done").update({"status": "replaced"}, synchronize_session=False)
        db.add_all(new_rows)
        db.flush()
    except Exception:
        db.rollback()
        for stored_key in written:
            Path(audio_storage.resolve_path(stored_key)).unlink(missing_ok=True)
        raise
    report["applied"] = True
    return report
