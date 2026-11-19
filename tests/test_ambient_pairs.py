"""Готовые A/B проверяем на искусственном WAV без провайдера и GPU."""
from pathlib import Path
import wave
from xml.etree import ElementTree as ET

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models
import app.v2.models
from app.db import Base
from app.models import AmbientTrack, AudioFile, ScriptBook, ScriptChapter, SoundMarker
from app.services.ambient_pairs import current_pairs, import_pair


@pytest.fixture
def studio(tmp_path, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "audio_storage_path", str(tmp_path / "audio"))
    monkeypatch.setattr(settings, "local_reserve_gb", 0)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(ScriptBook(id="book", title="Sample Book", language="en", source_filename="sample.txt", source_format="txt"))
        db.add(ScriptChapter(id="chapter", book_id="book", chapter_index=1, chapter_title="First"))
        db.add(SoundMarker(id="scene", book_id="book", chapter_id="chapter", segment_id="chapter:00000",
            kind="scene", status="active", payload={"mood": "calm"}, text_sha256="snapshot"))
        db.commit()
        yield db, tmp_path


def sources(tmp_path):
    paths = []
    for variant in ("a", "b"):
        path = tmp_path / (variant + ".wav")
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(8000)
            audio.writeframes((b"\0" if variant == "a" else b"\1") * 64000)
        paths.append(path)
    return paths


def imported(studio):
    db, root = studio
    a, b = sources(root)
    report = import_pair(db, chapter_id="chapter", marker_id="scene", variant_a=a, variant_b=b, apply=True)
    db.commit()
    return report


def test_dry_run_then_idempotent_pair_and_local_files(studio):
    db, root = studio
    a, b = sources(root)
    report = import_pair(db, chapter_id="chapter", marker_id="scene", variant_a=a, variant_b=b)
    assert not report["applied"] and not report["existing"]
    assert db.query(AudioFile).count() == 0
    assert not (root / "audio").exists()
    imported(studio)
    pair = current_pairs(db, "chapter")["scene"]
    assert set(pair) == {"A", "B"}
    assert pair["A"].canonical_filename != pair["B"].canonical_filename
    assert all(item.kind == "ambient" and item.location == "local" for item in pair.values())
    assert import_pair(db, chapter_id="chapter", marker_id="scene", variant_a=a, variant_b=b, apply=True)["existing"]
    assert db.query(AudioFile).count() == 2


def test_scene_edit_and_dismiss_invalidate_both_variants(studio):
    db, root = studio
    imported(studio)
    marker = db.get(SoundMarker, "scene")
    marker.payload = {"mood": "tense"}
    db.commit()
    assert current_pairs(db, "chapter") == {}
    imported(studio)
    assert current_pairs(db, "chapter")
    marker.status = "dismissed"
    db.commit()
    assert current_pairs(db, "chapter") == {}
    with pytest.raises(ValueError):
        import_pair(db, chapter_id="chapter", marker_id="scene", variant_a=root / "a.wav", variant_b=root / "b.wav", apply=True)


def test_invalid_second_file_cannot_publish_half_pair(studio):
    db, root = studio
    a, b = sources(root)
    b.write_bytes(b"RIFF")
    with pytest.raises((ValueError, EOFError, wave.Error)):
        import_pair(db, chapter_id="chapter", marker_id="scene", variant_a=a, variant_b=b, apply=True)
    assert db.query(AudioFile).count() == 0
    assert db.query(AmbientTrack).count() == 0


def test_pair_on_same_span_b_muted_and_archive_lists_both(studio, monkeypatch):
    from app.services import chapter_delivery
    from app.services.audition_session import build_session_xml
    db, _root = studio
    imported(studio)
    monkeypatch.setattr(chapter_delivery, "_spans_on_timeline", lambda *_: {"scene": (3.0, 1.25)})
    tracks = chapter_delivery.ambient_session_tracks(db, "chapter", {}, 5, relative=True)
    assert [track.name for track in tracks] == ["Ambient 1", "Ambient 2"]
    assert [track.muted for track in tracks] == [False, True]
    assert [[clip.start, clip.source_out] for track in tracks for clip in track.clips] == [[3.0, 1.25], [3.0, 1.25]]
    xml = build_session_xml(title="Sample", tracks=tracks)
    assert chapter_delivery.session_fingerprint(xml) == chapter_delivery.session_fingerprint(build_session_xml(title="Sample", tracks=tracks))
    tree = ET.fromstring(xml)
    muted = tree.findall(".//trackAudioParameters/component[@componentID='Audition.Mute']")
    assert len(muted) == 1
    assert muted[0].find("parameter[@name='mute']").get("parameterValue") == "1"
    archive_files = chapter_delivery.chapter_ambient_files(db, "chapter")
    assert {item.id for item in archive_files} == {item.id for item in current_pairs(db, "chapter")["scene"].values()}
    assert all(item.canonical_filename in xml for item in archive_files)


def test_new_pair_replaces_imports_without_deleting_files(studio):
    db, root = studio
    imported(studio)
    old = list(db.query(AudioFile))
    a, b = sources(root)
    with wave.open(str(a), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\2" * 32000)
    import_pair(db, chapter_id="chapter", marker_id="scene", variant_a=a, variant_b=b, apply=True)
    db.commit()
    assert db.query(AmbientTrack).filter_by(status="replaced").count() == 2
    assert db.query(AmbientTrack).filter_by(status="done").count() == 2
    from app.services.audio_storage import resolve_path
    assert all(Path(resolve_path(item.stored_key)).is_file() for item in old)


def test_migration_is_idempotent_and_preserves_legacy_rows():
    import importlib.util
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0071_ambient_pairs.py"
    spec = importlib.util.spec_from_file_location("pair_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE ambient_tracks (id TEXT PRIMARY KEY, status TEXT NOT NULL)")
        connection.exec_driver_sql("INSERT INTO ambient_tracks VALUES ('legacy', 'done')")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            migration.upgrade()
            assert connection.execute(sa.text("SELECT variant,pair_key,scene_fingerprint,status FROM ambient_tracks")).one() == ("", "", "", "done")
            migration.downgrade()
            migration.downgrade()
        assert connection.exec_driver_sql("SELECT id,status FROM ambient_tracks").one() == ("legacy", "done")


def test_mixed_legacy_scene_stays_audible(studio, monkeypatch):
    from app.services import chapter_delivery
    db, _root = studio
    imported(studio)
    db.add(SoundMarker(id="legacy-scene", book_id="book", chapter_id="chapter", segment_id="chapter:00001",
        kind="scene", status="active", payload={}, text_sha256="legacy"))
    db.add(AudioFile(id="legacy-audio", book_code="SAMPLE", chapter="First", original_filename="old.mp3",
        canonical_filename="old.mp3", stored_key="old.mp3", mime_type="audio/mpeg", size_bytes=10,
        kind="ambient", role="Ambient", duration_seconds=5))
    db.add(AmbientTrack(book_id="book", chapter_id="chapter", marker_id="legacy-scene",
        audio_file_id="legacy-audio", status="done"))
    db.commit()
    monkeypatch.setattr(chapter_delivery, "_spans_on_timeline", lambda *_: {"scene": (0.0, 2.0), "legacy-scene": (2.0, 5.0)})
    tracks = chapter_delivery.ambient_session_tracks(db, "chapter", {}, 7)
    assert not tracks[0].muted and tracks[1].muted
    assert "old.mp3" in [clip.name for clip in tracks[0].clips]
    assert "old.mp3" not in [clip.name for clip in tracks[1].clips]
