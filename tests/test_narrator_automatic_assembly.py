"""Настоящий проект из синтетических файлов: рассказчик запускает монтаж и проверку."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import settings
from app.db import Base
from app.models import AsrAudit, AsrJob, AudioFile, BackgroundRun, Character, ScriptBook, ScriptChapter
from app.services import asr_audit, asr_audit_lifecycle as lifecycle, chapter_delivery as delivery, nas_health
from app.services.chapter_assembly import assemble_narrator_chapter, archive_pending_chapters
from app.services.role_identity import NARRATOR_REF
from app.services.shared_runtime import NARRATOR_DISPLAY_NAME
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture(params=["local", "nas"])
def studio(tmp_path, monkeypatch, request):
    local, nas = tmp_path / "audio", tmp_path / "nas"
    local.mkdir()
    nas.mkdir()
    monkeypatch.setattr(settings, "audio_storage_path", str(local))
    monkeypatch.setattr(settings, "audio_nas_path", str(nas) if request.param == "nas" else "")
    monkeypatch.setattr(settings, "asr_audit_enabled", True)
    monkeypatch.setattr("app.services.provider_keys.provider_key", lambda provider: "test-key")
    nas_health.record_probe(True)
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'assembly.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(lifecycle, "SessionLocal", factory)
    queued = []
    def enqueue(*args, **kw):
        kw.pop("job_timeout", None)
        kw.pop("result_ttl", None)
        queued.append(kw)
        return SimpleNamespace(id="job-" + kw["run_id"])
    monkeypatch.setattr(lifecycle, "get_queue", lambda _: SimpleNamespace(enqueue=enqueue))
    with factory() as db:
        db.add(ScriptBook(id="assembly-book", title="Sample Bridge", language="en", role_identity_mode="typed_v1",
                          source_filename="sample.txt", source_format="txt", chapter_title_actor_name="Title Actor"))
        chapter = ScriptChapter(id="assembly-chapter", book_id="assembly-book", chapter_index=1,
                                chapter_title="Chapter 1", status="published")
        db.add(chapter)
        db.add(Character(id="speaker", book_id="assembly-book", name="Speaker", actor_name="Sample Actor"))
        for ordinal, text, name, ref in [
                (0, "The amber lantern glowed.", NARRATOR_DISPLAY_NAME, NARRATOR_REF),
                (1, "Wait beside the bridge.", "Speaker", "character:speaker"),
                (2, "The gate opened slowly.", NARRATOR_DISPLAY_NAME, NARRATOR_REF)]:
            sid = f"assembly-chapter:{ordinal}"
            db.add(V2Segment(id=sid, chapter_id=chapter.id, book_id=chapter.book_id, ordinal=ordinal,
                             text=text, kind="paragraph"))
            db.add(V2Attribution(id=f"assembly-attr-{ordinal}", segment_id=sid, version=1,
                                 speaker=name, role_ref=ref, span_start=0, span_end=len(text)))
        payload = b"synthetic audio content"
        (local / "narrator.wav").write_bytes(payload)
        db.add(AudioFile(id="assembly-narrator", role_book_id="assembly-book", role_ref=NARRATOR_REF,
            role=NARRATOR_DISPLAY_NAME, book_code="SB", chapter="Chapter 1", kind="take",
            original_filename="narrator.wav", canonical_filename="narrator.wav", stored_key="narrator.wav",
            mime_type="audio/wav", size_bytes=len(payload), md5=hashlib.md5(payload).hexdigest(), duration_seconds=10))
        db.add(AsrJob(id="narrator-job", audio_file_id="assembly-narrator", chapter_id=chapter.id, status="done", language="en",
            alignment_json=json.dumps({"lines": [
                {"matched": True, "start": 0., "end": 2.}, {"matched": False}]})))
        db.commit()
    yield factory, local, nas, queued
    engine.dispose()


def test_narrator_alone_builds_a_real_draft_and_reserves_audit_once(studio):
    factory, local, nas, queued = studio
    with factory() as db:
        assert delivery.chapter_recording_status(db, "assembly-chapter")["ready"] is False
        result = archive_pending_chapters(db)
        assert result["archived"] == 1
        chapter = db.get(ScriptChapter, "assembly-chapter")
        root = nas if chapter.session_location == "nas" else local
        project = root / chapter.session_key
        xml = project.read_text(encoding="utf-8-sig")
        parsed = ET.fromstring(xml)
        assert "MISSING · Speaker" in xml and "The gate opened slowly." in xml
        assert all(track.find("audioClip") is None for track in parsed.findall("./session/tracks/audioTrack")
                   if track.findtext("trackParameters/name") == "Названия глав")
        assert not delivery.chapter_recording_status(db, chapter.id)["ready"]
        for file in parsed.findall("./files/file"):
            assert (project.parent / file.attrib["relativePath"]).read_bytes() == (local / "narrator.wav").read_bytes()
        assert lifecycle.assembled_audit_snapshot(db, chapter.id) is not None
        original_key = chapter.session_key
        assert archive_pending_chapters(db)["archived"] == 0
        assert db.get(ScriptChapter, chapter.id).session_key == original_key
    assert len(queued) == 1 and queued[0]["language"] == "en"
    assert queued[0]["audit_policy_version"] == asr_audit.AUDIT_POLICY_VERSION


@pytest.mark.parametrize("state", ["missing", "queued", "running", "failed"])
def test_no_project_or_payment_before_narrator_asr_finishes(studio, state):
    factory, local, nas, queued = studio
    with factory() as db:
        if state == "missing":
            db.query(AsrJob).delete()
        else:
            db.get(AsrJob, "narrator-job").status = state
        db.commit()
        assert archive_pending_chapters(db)["archived"] == 0
        assert db.get(ScriptChapter, "assembly-chapter").session_archived_at is None
    assert queued == [] and not list(local.rglob("*.sesx")) and not list(nas.rglob("*.sesx"))


def test_actor_file_without_narrator_cannot_trigger_assembly(studio):
    factory, _, _, queued = studio
    with factory() as db:
        take = db.get(AudioFile, "assembly-narrator")
        take.role, take.role_ref = "Speaker", "character:speaker"
        db.commit()
        assert archive_pending_chapters(db)["archived"] == 0
        assert queued == []


def test_new_revision_keeps_edited_manual_project_and_reuses_unchanged_media(studio):
    factory, local, nas, _ = studio
    with factory() as db:
        first = assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
        chapter = db.get(ScriptChapter, "assembly-chapter")
        root = nas if chapter.session_location == "nas" else local
        old = root / first["session_key"]
        assert old.name == "глава01_ver1.sesx"
        manual = old.with_name("ГЛАВА01_ver10.SESX")
        manual.write_bytes(b"manual session")
        media = [old.parent / entry.attrib["relativePath"] for entry in ET.fromstring(old.read_text(encoding="utf-8-sig")).findall("./files/file")]
        inode = media[0].stat().st_ino
        old.write_bytes(b"manual editing must survive")
        db.get(V2Segment, "assembly-chapter:2").text = "Another synthetic sentence."
        db.get(V2Attribution, "assembly-attr-2").span_end = len("Another synthetic sentence.")
        db.commit()
        second = assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
        assert second["written"] and (root / second["session_key"]).name == "глава01_ver11.sesx"
        assert manual.read_bytes() == b"manual session"
        assert old.read_bytes() == b"manual editing must survive"
        assert len(list(old.parent.glob("*.wav"))) == 1
        assert media[0].stat().st_ino == inode
        assert len(list(old.parent.glob("*.sesx"))) == 2
        renamed = root / second["session_key"]
        renamed.write_bytes(b"final mix")
        final = renamed.rename(old.with_name("Финал.sesx"))
        third = assemble_narrator_chapter(db, "assembly-chapter")
        assert third["written"] and (root / third["session_key"]).name == "глава01_ver12.sesx"
        assert final.read_bytes() == b"final mix" and not renamed.exists()


def test_confirmed_current_audit_causes_new_session_with_real_markers(studio, monkeypatch):
    factory, local, nas, _ = studio
    with factory() as db:
        first = assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
        chapter = db.get(ScriptChapter, "assembly-chapter")
        fingerprint = asr_audit.narrator_input_fingerprint(db, chapter.id)
        flag = {"index": 0, "segment_id": "assembly-chapter:0", "text": "The amber lantern glowed.",
                "confirmed": True, "missing_words": ["amber"], "note": "lexical mismatch",
                "word_anchors": [{"source_audio_file_id": "assembly-narrator", "start": .5, "end": .6,
                                  "word": "amber", "approximate": True}]}
        db.add(AsrAudit(id="confirmed-audit", chapter_id=chapter.id, audio_file_id="assembly-narrator", status="done",
            lines_json=json.dumps({"audit_policy_version": asr_audit.AUDIT_POLICY_VERSION,
                                   "narrator_input_fingerprint": fingerprint}), findings_json=json.dumps({"flags": [flag]})))
        db.commit()
        delivery.mark_session_outdated(db, chapter.id)
        db.commit()
        assert archive_pending_chapters(db)["archived"] == 1
        chapter = db.get(ScriptChapter, chapter.id)
        root = nas if chapter.session_location == "nas" else local
        assert "NARRATOR · text mismatch" in (root / chapter.session_key).read_text(encoding="utf-8-sig")
        assert (root / first["session_key"]).exists()
        assert db.query(BackgroundRun).count() == 0


def test_old_policy_unknown_payment_cannot_be_bypassed_by_changed_input(studio):
    factory, _, _, queued = studio
    with factory() as db:
        assemble_narrator_chapter(db, "assembly-chapter")
        db.add(BackgroundRun(id="uncertain-run", run_key="asr-audit:assembly-chapter", job_kind="asr_audit",
            entity_type="script_chapter", entity_id="assembly-chapter", status="failed",
            meta_json=json.dumps({"payment_state": "possibly_paid", "narrator_input_fingerprint": "old-input"})))
        db.commit()
    assert lifecycle.enqueue_audit("assembly-chapter") is None and queued == []


def test_append_only_registry_migration_is_idempotent_and_preserves_existing_chapters():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0069_narrator_assembly.py"
    spec = importlib.util.spec_from_file_location("assembly_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(sa.text("CREATE TABLE script_chapters(id TEXT PRIMARY KEY, session_sha256 TEXT)"))
        connection.execute(sa.text("INSERT INTO script_chapters VALUES ('legacy', 'old-fingerprint')"))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
        row = connection.execute(sa.text("SELECT * FROM script_chapters")).mappings().one()
        assert row["session_sha256"] == "old-fingerprint" and row["session_key"] == ""
    engine.dispose()


@pytest.mark.parametrize("status,calls,cost_complete", [("running", 0, False), ("failed", 1, True), ("skipped", 0, False)])
def test_uncertain_standalone_audit_blocks_automatic_new_policy(studio, status, calls, cost_complete):
    factory, _, _, queued = studio
    with factory() as db:
        assemble_narrator_chapter(db, "assembly-chapter")
        db.add(AsrAudit(chapter_id="assembly-chapter", status=status, lines_json=json.dumps({
            "narrator_input_fingerprint": "old-classification-input", "stt_calls": calls,
            "cost_complete": cost_complete})))
        db.commit()
    assert lifecycle.enqueue_audit("assembly-chapter") is None and queued == []


def test_deleted_registered_session_does_not_authorize_payment(studio):
    factory, local, nas, queued = studio
    with factory() as db:
        result = assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
        (nas if result["session_location"] == "nas" else local).joinpath(result["session_key"]).unlink()
    assert lifecycle.enqueue_audit("assembly-chapter") is None and queued == []


def test_complete_assembly_worker_confirmation_and_marker_revision(studio, monkeypatch):
    from app import worker_tasks

    factory, local, nas, queued = studio
    with factory() as db:
        assert archive_pending_chapters(db)["archived"] == 1
        first_key = db.get(ScriptChapter, "assembly-chapter").session_key
    costs, actor_notices = [], []
    monkeypatch.setattr(asr_audit, "_notify_narrator", lambda *a, **kw: actor_notices.append(kw))
    def transcribe(path, **kw):
        costs.append(kw.get("model"))
        words = [{"word": word, "start": i * .4, "end": i * .4 + .3}
                 for i, word in enumerate(["The", "lantern", "glowed"])]
        return {"text": "The lantern glowed.", "segments": [{"text": "The lantern glowed.",
                "start": 0., "end": 1.2, "words": words}]}, .25
    def judge(system, user, schema, **kw):
        if "results" in schema["properties"]:
            return {"results": [{"index": 0, "confirmed": True, "note": "amber absent in second hearing"}]}
        return {"verdicts": [
            {"index": 0, "status": "partial", "missing_words": ["amber"], "note": "amber absent"},
            {"index": 1, "status": "ok", "missing_words": [], "note": ""}]}
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    monkeypatch.setattr(asr_audit, "_transcribe_fresh", transcribe)
    monkeypatch.setattr(asr_audit, "default_judge_ask", judge)
    monkeypatch.setattr(asr_audit, "_cut_window", lambda *a, **kw: None)
    monkeypatch.setattr("app.services.mirror_wakeup.request_mirror_wakeup", lambda: True)
    worker_tasks.perform_asr_audit_task(**queued[0])
    with factory() as db:
        audit = db.query(AsrAudit).one()
        assert audit.status == "done" and audit.stt_cost_rub == .5
        assert json.loads(audit.findings_json)["confirmed"] == 1
        assert db.query(BackgroundRun).one().status == "done"
        assert archive_pending_chapters(db)["archived"] == 1
        chapter = db.get(ScriptChapter, "assembly-chapter")
        root = nas if chapter.session_location == "nas" else local
        assert chapter.session_key != first_key
        assert "NARRATOR · text mismatch" in (root / chapter.session_key).read_text(encoding="utf-8-sig")
        assert "NARRATOR · text mismatch" not in (root / first_key).read_text(encoding="utf-8-sig")
        assert archive_pending_chapters(db)["archived"] == 0
    assert len(costs) == 2 and len(queued) == 1 and actor_notices == []


def test_changed_cached_media_and_symlink_never_replace_user_files(studio):
    factory, local, nas, _ = studio
    with factory() as db:
        first = assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
        root = nas if first["session_location"] == "nas" else local
        old = root / first["session_key"]
        media = old.parent / ET.fromstring(old.read_text(encoding="utf-8-sig")).find("./files/file").attrib["relativePath"]
        media.write_bytes(b"manual media edits")
        db.get(V2Segment, "assembly-chapter:2").text = "A new synthetic gap."
        db.get(V2Attribution, "assembly-attr-2").span_end = len("A new synthetic gap.")
        db.commit()
        second = assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
        assert second["written"] and media.read_bytes() == b"manual media edits"
        assert len(list(old.parent.glob("*.sesx"))) == 2
        new_xml = ET.fromstring((root / second["session_key"]).read_text(encoding="utf-8-sig"))
        for entry in new_xml.findall("./files/file"):
            assert (old.parent / entry.attrib["relativePath"]).read_bytes() == (local / "narrator.wav").read_bytes()


def test_zero_match_narrator_publishes_raw_draft_with_gaps_without_paid_audit(studio):
    factory, local, nas, queued = studio
    with factory() as db:
        db.get(AsrJob, "narrator-job").alignment_json = json.dumps({"lines": [{"matched": False}, {"matched": False}]})
        db.commit()
        assert archive_pending_chapters(db)["archived"] == 1
        chapter = db.get(ScriptChapter, "assembly-chapter")
        root = nas if chapter.session_location == "nas" else local
        xml = (root / chapter.session_key).read_text(encoding="utf-8-sig")
        assert "MISSING" in xml and "The amber lantern glowed." in xml
        assert ET.fromstring(xml).findall("./files/file")
        assert lifecycle.assembled_audit_snapshot(db, chapter.id) is None
    assert queued == []


def test_automatic_revision_preserves_effect_preset_cdata(studio, monkeypatch):
    factory, local, nas, _ = studio
    build = delivery.build_chapter_session_with_stats
    def with_preset(*a, **kw):
        xml, skipped = build(*a, **kw)
        return xml.replace("</sesx>", "<preset><![CDATA[<effect attr=\"value\">gain & mix</effect>]]></preset></sesx>"), skipped
    monkeypatch.setattr(delivery, "build_chapter_session_with_stats", with_preset)
    with factory() as db:
        result = assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
        root = nas if result["session_location"] == "nas" else local
        xml = (root / result["session_key"]).read_text(encoding="utf-8-sig")
        assert '<![CDATA[<effect attr="value">gain & mix</effect>]]>' in xml



def test_known_completed_stale_audit_allows_only_new_input(studio, monkeypatch):
    factory, _, _, queued = studio
    with factory() as db:
        assemble_narrator_chapter(db, "assembly-chapter")
        fingerprint = asr_audit.narrator_input_fingerprint(db, "assembly-chapter")
        db.add(AsrAudit(chapter_id="assembly-chapter", status="skipped", stt_cost_rub=1.,
            error_message="narrator_input_changed_after_payment", lines_json=json.dumps({
                "narrator_input_fingerprint": fingerprint, "stt_calls": 2, "cost_complete": True,
                "audit_policy_version": asr_audit.AUDIT_POLICY_VERSION})))
        db.add(BackgroundRun(id="completed-stale", run_key="asr-audit:assembly-chapter", job_kind="asr_audit",
            entity_type="script_chapter", entity_id="assembly-chapter", status="skipped", meta_json=json.dumps({
                "narrator_input_fingerprint": fingerprint, "payment_state": "completed",
                "audit_policy_version": asr_audit.AUDIT_POLICY_VERSION})))
        db.commit()
    assert lifecycle.enqueue_audit("assembly-chapter") is None
    with factory() as db:
        text = "A new synthetic missing sentence."
        db.get(V2Segment, "assembly-chapter:2").text = text
        db.get(V2Attribution, "assembly-attr-2").span_end = len(text)
        db.commit()
        assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
    assert lifecycle.enqueue_audit("assembly-chapter") and len(queued) == 1


def test_working_layout_preserves_titles_pairs_presets_and_existing_folders(studio, monkeypatch):
    from app.models import SoundMarker
    from app.services import session_template
    from app.services.ambient_pairs import import_pair
    from app.services.chapter_titles import TITLE_ROLE
    from app.services.role_identity import TITLE_REF
    from tests.test_ambient_pairs import sources
    from tests.test_session_template import _template, _track, VO

    factory, local, nas, _ = studio
    with factory() as db:
        # Имя персонажа совпадает с пустой служебной дорожкой: обе нужны в XML.
        db.get(Character, "speaker").name = "Track 1"
        db.get(V2Attribution, "assembly-attr-1").speaker = "Track 1"
        db.add(V2Segment(id="assembly-title", book_id="assembly-book", chapter_id="assembly-chapter",
                         ordinal=-1, kind="heading", text="Chapter 1"))
        for aid, role, ref in [("title-take", TITLE_ROLE, TITLE_REF), ("actor-take", "Track 1", "character:speaker")]:
            payload = ("synthetic " + aid).encode()
            (local / (aid + ".wav")).write_bytes(payload)
            db.add(AudioFile(id=aid, role_book_id="assembly-book", role_ref=ref, role=role,
                book_code="SB", chapter="Chapter 1", kind="take", original_filename=aid + ".wav",
                canonical_filename=aid + ".wav", stored_key=aid + ".wav", mime_type="audio/wav",
                size_bytes=len(payload), md5=hashlib.md5(payload).hexdigest(), duration_seconds=10))
            lines = [{"matched": True, "start": 1., "end": 2.}]
            if aid == "title-take":
                lines[0]["takes"] = [{"start": 1., "end": 2.}, {"start": 4., "end": 5.}]
            db.add(AsrJob(id=aid + "-job", audio_file_id=aid, chapter_id="assembly-chapter",
                         status="done", language="en", alignment_json=json.dumps({"lines": lines})))
        db.add(SoundMarker(id="assembly-scene", book_id="assembly-book", chapter_id="assembly-chapter",
            segment_id="assembly-chapter:0", kind="scene", status="active", payload={}, text_sha256="sample"))
        db.commit()
        a, b = sources(local)
        monkeypatch.setattr(settings, "local_reserve_gb", 0)
        import_pair(db, chapter_id="assembly-chapter", marker_id="assembly-scene", variant_a=a, variant_b=b, apply=True)
        db.commit()
        monkeypatch.setattr(delivery, "_spans_on_timeline", lambda *_: {"assembly-scene": (3., 2.)})
        template = session_template.parse_template(_template(_track("Track 1", [VO(0)])))
        monkeypatch.setattr(session_template, "load_template", lambda: template)
        root = nas if str(settings.audio_nas_path or "") else local
        folder = root / "SB" / "Chapter01"
        folder.mkdir(parents=True, exist_ok=True)
        for subfolder in ("Imported Files", "Conformed Files", "assembly-media"):
            (folder / subfolder).mkdir()
            (folder / subfolder / "manual.wav").write_bytes(b"keep manual media")
        (folder / "глава01_ver7.sesx").write_bytes(b"keep manual session")
        first = assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
        project = root / first["session_key"]
        parsed = ET.fromstring(project.read_text(encoding="utf-8-sig"))
        tracks = parsed.findall("./session/tracks/audioTrack")
        names = [track.findtext("trackParameters/name") for track in tracks]
        assert names == ["Ambient 1", "Ambient 2", "FX", TITLE_ROLE, "Track 1", NARRATOR_DISPLAY_NAME,
                         "Track 1", *delivery.EXTRA_TRACKS]
        assert project.name == "глава01_ver8.sesx"
        assert tracks[4].find("audioClip") is None and tracks[4].find("effectsRack") is None
        assert tracks[4].find("trackAudioParameters").get("audioChannelType") == "stereo"
        assert tracks[6].find("audioClip") is not None and tracks[6].find("effectsRack") is not None
        assert tracks[0].find("trackAudioParameters/component[@id='trackMute']") is None
        assert tracks[1].find("trackAudioParameters/component[@id='trackMute']") is not None
        assert len(tracks[3].findall("audioClip")) == 2
        files = {entry.get("id"): entry for entry in parsed.findall("./files/file")}
        assert {(project.parent / entry.get("relativePath")).read_bytes() for entry in files.values()} == {
            (local / name).read_bytes() for name in ("narrator.wav", "title-take.wav", "actor-take.wav", "a.wav", "b.wav")}
        expected_subfolders = {0: ".", 1: ".", 3: "Imported Files", 5: ".", 6: "Conformed Files"}
        for index, expected in expected_subfolders.items():
            for clip in tracks[index].findall("audioClip"):
                entry = files[clip.get("fileID")]
                relative = Path(entry.get("relativePath"))
                assert str(relative.parent) == expected
                assert (project.parent / relative).read_bytes() == Path(entry.get("absolutePath")).read_bytes()
        snapshot = project.read_bytes()
        db.get(V2Segment, "assembly-chapter:2").text = "A changed ending."
        db.get(V2Attribution, "assembly-attr-2").span_end = len("A changed ending.")
        db.commit()
        second = assemble_narrator_chapter(db, "assembly-chapter")
        db.commit()
        assert Path(second["session_key"]).name == "глава01_ver9.sesx"
        assert project.read_bytes() == snapshot
        assert assemble_narrator_chapter(db, "assembly-chapter")["reason"] == "unchanged"
        for subfolder in ("Imported Files", "Conformed Files", "assembly-media"):
            assert (folder / subfolder / "manual.wav").read_bytes() == b"keep manual media"
        assert (folder / "глава01_ver7.sesx").read_bytes() == b"keep manual session"
        ordinary = ET.fromstring(delivery.build_chapter_session(db, "assembly-chapter", relative=True).lstrip("\ufeff"))
        assert ordinary.find("./session/tracks/audioTrack").findtext("trackParameters/name") not in {"Ambient 1", "Ambient 2", "FX"}
        assert all("/" not in item.get("relativePath") for item in ordinary.findall("./files/file"))


def test_existing_legacy_layout_snapshot_is_unchanged_after_upgrade(studio, monkeypatch):
    from app.services.chapter_assembly import _inputs

    factory, local, nas, _ = studio
    with factory() as db:
        state, reason = _inputs(db, "assembly-chapter")
        assert not reason
        chapter, legacy_xml, _sources, digest, _skipped = state
        root = nas if str(settings.audio_nas_path or "") else local
        key = "SB/Chapter01/глава01_ver3.sesx"
        path = root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(legacy_xml, encoding="utf-8")
        chapter.session_key, chapter.session_location = key, "nas" if root == nas else "local"
        chapter.session_input_sha256 = digest
        chapter.session_archived_at = utcnow_naive()
        db.commit()
        previous = path.read_bytes()
        build = delivery.build_chapter_session_with_stats
        def unchanged_only(*args, **kwargs):
            assert not kwargs.get("working_layout"), "unchanged snapshot must not render or publish a replacement"
            return build(*args, **kwargs)
        monkeypatch.setattr(delivery, "build_chapter_session_with_stats", unchanged_only)
        assert assemble_narrator_chapter(db, chapter.id)["reason"] == "unchanged"
        assert path.read_bytes() == previous
        assert list(path.parent.glob("*.sesx")) == [path]


@pytest.mark.parametrize("collision", ["symlink", "directory"])
def test_working_media_collision_preserves_foreign_entry(studio, collision):
    from app.services.chapter_assembly import _media
    import os

    factory, local, _nas, _ = studio
    with factory() as db:
        item = db.get(AudioFile, "assembly-narrator")
        name = hashlib.sha256((item.id + ":" + item.md5).encode()).hexdigest()[:32] + ".wav"
        folder = local / "collision"
        folder.mkdir()
        existing = folder / name
        if collision == "symlink":
            existing.symlink_to(local / "narrator.wav")
        else:
            existing.mkdir()
        descriptor = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            copied, _digest, _size = _media(descriptor, item)
        finally:
            os.close(descriptor)
        assert copied != name
        assert (folder / copied).read_bytes() == (local / "narrator.wav").read_bytes()
        assert existing.is_symlink() if collision == "symlink" else existing.is_dir()


def test_renamed_media_directory_prevents_publication(studio, monkeypatch):
    from app.services import chapter_assembly

    factory, local, nas, queued = studio
    root = nas if settings.audio_nas_path else local
    chapter = root / "SB" / "Chapter01"
    voices = chapter / "Conformed Files"
    moved = chapter / "manual voices"
    voices.mkdir(parents=True)
    (voices / "manual.wav").write_bytes(b"original edit")
    media = chapter_assembly._media
    copied = []

    def rename_after_first_voice(directory, item):
        result = media(directory, item)
        if item.id.startswith("voice-"):
            copied.append(result[0])
            if len(copied) == 1:
                voices.rename(moved)
                voices.mkdir()
                (voices / "manual.wav").write_bytes(b"new edit")
        return result

    monkeypatch.setattr(chapter_assembly, "_media", rename_after_first_voice)
    with factory() as db:
        for index in range(2):
            aid = f"voice-{index}"
            role = f"Actor {index}"
            ref = "character:" + aid
            db.add(Character(id=aid, book_id="assembly-book", name=role))
            sid = f"voice-line-{index}"
            db.add(V2Segment(id=sid, chapter_id="assembly-chapter", book_id="assembly-book",
                             ordinal=3 + index, text="Hello.", kind="paragraph"))
            db.add(V2Attribution(id="attr-" + aid, segment_id=sid, version=1, speaker=role,
                                 role_ref=ref, span_start=0, span_end=6))
            (local / (aid + ".wav")).write_bytes(aid.encode())
            db.add(AudioFile(id=aid, role_book_id="assembly-book", role_ref=ref, role=role,
                book_code="SB", chapter="Chapter 1", kind="take", original_filename=aid + ".wav",
                canonical_filename=aid + ".wav", stored_key=aid + ".wav", mime_type="audio/wav",
                size_bytes=len(aid), md5=hashlib.md5(aid.encode()).hexdigest(), duration_seconds=2))
            db.add(AsrJob(id="job-" + aid, audio_file_id=aid, chapter_id="assembly-chapter", status="done",
                          alignment_json=json.dumps({"lines": [{"matched": True, "start": 0., "end": 1.}]})))
        db.commit()
        result = assemble_narrator_chapter(db, "assembly-chapter")
        assert result["reason"] == "assembly_directory_changed" and not result["written"]
        db.commit()
        assert not db.get(ScriptChapter, "assembly-chapter").session_key
    assert len(copied) == 2 and all((moved / name).is_file() for name in copied)
    assert (moved / "manual.wav").read_bytes() == b"original edit"
    assert (voices / "manual.wav").read_bytes() == b"new edit"
    assert not list(chapter.glob("*.sesx")) and not queued
