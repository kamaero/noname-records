"""Полная пересъёмка исключает старый звук в реальном проекте и покрытиях."""
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
from defusedxml import ElementTree as ET

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from tests.test_narrator_automatic_assembly import studio
from app.models import AsrJob, AudioFile, BotUpload, Character, ScriptBook, ScriptChapter
from app.services import asr_borrow, chapter_delivery as delivery
from app.services.chapter_assembly import narrator_ready_reason
from app.services.narrator_recording import chapter_takes, clean_alignment, resolve_mode
from app.services.role_identity import NARRATOR_REF
from app.services.shared_runtime import NARRATOR_DISPLAY_NAME


def add_take(db, name, mode, offset, matched=(False, False), status="done", role=NARRATOR_DISPLAY_NAME, ref=NARRATOR_REF):
    old = db.get(AudioFile, "assembly-narrator")
    item = AudioFile(id=name, role_book_id=old.role_book_id, role_ref=ref, role=role, book_code=old.book_code,
        chapter=old.chapter, kind="take", original_filename=f"Sample_Chapter1_Narrator_{name}.wav",
        canonical_filename=f"{name}.wav", stored_key=old.stored_key, mime_type="audio/wav",
        size_bytes=old.size_bytes, md5=old.md5, duration_seconds=10,
        uploaded_at=old.uploaded_at + timedelta(seconds=offset), correction_mode=mode)
    db.add(item)
    if status is not None:
        lines = [{"index": index, "text": text, "matched": found, "score": 1. if found else 0.,
                  "start": index * 3., "end": index * 3. + 2., "takes": []}
                 for index, (text, found) in enumerate(zip(
                     ["The amber lantern glowed.", "The gate opened slowly."], matched))]
        db.add(AsrJob(id="job-" + name, audio_file_id=name, chapter_id="assembly-chapter", status=status,
            expected_role=role, role_ref=ref, language="en", alignment_json=json.dumps({"lines": lines,
                "total": 2, "matched": sum(matched), "missing": [i for i, found in enumerate(matched) if not found],
                "coverage": sum(matched) / 2})))
    db.commit()
    return item


def clips(db):
    return [clip for role, clip in delivery._script_layout(db, "assembly-chapter")[3] if role == NARRATOR_DISPLAY_NAME]


def test_new_full_omission_does_not_fall_back_to_old_base_or_old_fix(studio):
    factory, _, _, _ = studio
    with factory() as db:
        old_job = db.get(AsrJob, "narrator-job")
        payload = json.loads(old_job.alignment_json)
        payload["lines"][1] = {"matched": True, "start": 4., "end": 6.}
        old_job.alignment_json = json.dumps(payload)
        add_take(db, "old_fix", "", 1, (True, True))
        new = add_take(db, "full", "full", 2, (True, False))
        assert {clip.audio_file_id for clip in clips(db)} == {new.id}
        assert len(clips(db)) == 1
        current = delivery.chapter_recording_status(db, "assembly-chapter")
        row = next(row for row in current["roles"] if row["role"] == NARRATOR_DISPLAY_NAME)
        assert row["matched_lines"] == 1 and row["missing_lines"] == 1
        xml, _ = delivery.build_chapter_session_with_stats(db, "assembly-chapter", include_missing=True, include_unplaced=True)
        assert "The gate opened slowly." in xml and "MISSING" in xml
        assert db.query(AudioFile).count() == 3
        assert {audio.id for audio in chapter_takes(db, db.get(ScriptChapter, "assembly-chapter"))} == {new.id}
        assert {audio.id for _, audio, _ in asr_borrow.chapter_done_jobs(db, "assembly-chapter")} == {new.id}


@pytest.mark.parametrize("status", [None, "queued", "running", "failed"])
def test_pending_or_failed_full_never_restores_old_clips(studio, status):
    factory, _, _, _ = studio
    with factory() as db:
        add_take(db, "full", "full", 2, status=status)
        assert clips(db) == []
        assert narrator_ready_reason(db, db.get(ScriptChapter, "assembly-chapter")) in {"narrator_asr_pending", "narrator_asr_failed"}
        xml, _ = delivery.build_chapter_session_with_stats(db, "assembly-chapter", include_missing=True, include_unplaced=True)
        assert not ET.fromstring(xml).findall("./files/file")


def test_explicit_patch_can_complete_current_full_but_second_full_excludes_it(studio):
    factory, _, _, _ = studio
    with factory() as db:
        add_take(db, "full", "full", 2, (True, False))
        add_take(db, "patch", "patch", 3, (False, True))
        assert {clip.audio_file_id for clip in clips(db)} == {"full", "patch"}
        narrator = next(row for row in delivery.chapter_recording_status(db, "assembly-chapter")["roles"] if row["role"] == NARRATOR_DISPLAY_NAME)
        assert narrator["matched_lines"] == 2 and narrator["missing_lines"] == 0
        from app.services.chapter_coverage_report import build_chapter_report
        assert build_chapter_report(db, "assembly-chapter")["missing"] == []
        add_take(db, "full2", "full", 4, (True, False))
        assert {clip.audio_file_id for clip in clips(db)} == {"full2"}


def test_cached_old_or_cross_role_borrow_does_not_fill_full_narrator(studio):
    factory, _, _, _ = studio
    with factory() as db:
        add_take(db, "donor", "", 1, (True, True), role="Speaker", ref="character:speaker")
        add_take(db, "full", "full", 2, (True, True))
        job = db.get(AsrJob, "job-full")
        payload = json.loads(job.alignment_json)
        payload["lines"][0]["source_audio_file_id"] = "assembly-narrator"
        payload["lines"][1]["source_audio_file_id"] = "donor"
        job.alignment_json = json.dumps(payload)
        db.commit()
        assert clips(db) == []
        rows = asr_borrow.chapter_done_jobs(db, "assembly-chapter")
        full = next(al for _, audio, al in rows if audio.id == "full")
        assert full["matched"] == 0 and full["missing"] == [0, 1]


def test_typed_character_named_narrator_is_unchanged(studio):
    factory, _, _, _ = studio
    with factory() as db:
        db.add(Character(id="literal", book_id="assembly-book", name="Narrator", actor_name="Other Actor"))
        db.commit()
        add_take(db, "literal", "", 1, role="Narrator", ref="character:literal")
        add_take(db, "full", "full", 2)
        assert {item.id for item in chapter_takes(db, db.get(ScriptChapter, "assembly-chapter"))} == {"literal", "full"}
        assert resolve_mode(db, book_code="SB", role="Narrator", chapter="Chapter 1", filename="Sample_Chapter1_Narrator_fix.wav") == ""
        with pytest.raises(ValueError, match="requires_narrator"):
            resolve_mode(db, book_code="SB", role="Narrator", chapter="Chapter 1", filename="Sample_Chapter1_Narrator_fix.wav", mode="full")


def test_new_narrator_fix_defaults_full_and_explicit_patch_is_separate(studio):
    factory, _, _, _ = studio
    with factory() as db:
        args = dict(book_code="SB", role=NARRATOR_DISPLAY_NAME, chapter="Chapter 1", filename="Sample_Chapter1_Narrator_fix.wav")
        assert resolve_mode(db, **args) == "full"
        assert resolve_mode(db, **args, mode="patch") == "patch"
        args["filename"] = "Sample_Narrator_fix.wav"
        with pytest.raises(ValueError, match="named_chapter"):
            resolve_mode(db, **args)


def test_admitted_failed_full_blocks_old_audio_before_asr_has_stored_a_new_file(studio):
    factory, _, _, _ = studio
    with factory() as db:
        old = db.get(AudioFile, "assembly-narrator")
        db.add(Character(id="system", book_id="assembly-book", name=NARRATOR_DISPLAY_NAME, actor_name="Sample Actor"))
        db.add(BotUpload(id="intent", telegram_user_id="1", file_id="test", kind="document", purpose="take", state="failed",
            correction_mode="full", book_id="assembly-book", character_id="system", role_name=NARRATOR_DISPLAY_NAME,
            file_name="Sample_Chapter1_Narrator_fix.wav", created_at=old.uploaded_at + timedelta(seconds=1)))
        db.commit()
        assert chapter_takes(db, db.get(ScriptChapter, "assembly-chapter")) == []
        assert clips(db) == []
        assert narrator_ready_reason(db, db.get(ScriptChapter, "assembly-chapter")) == "narrator_intake_failed"


def test_migration_retains_historical_defaults_and_is_idempotent():
    path = Path("alembic/versions/0070_narrator_correction_mode.py")
    spec = importlib.util.spec_from_file_location("migration0070", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE audio_files (id TEXT, original_filename TEXT)")
        connection.exec_driver_sql("INSERT INTO audio_files VALUES ('old','Narrator_fix.wav')")
        connection.exec_driver_sql("CREATE TABLE bot_uploads (id TEXT)")
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            module.upgrade()
        assert connection.exec_driver_sql("SELECT correction_mode, correction_source_id FROM audio_files").one() == ("", "")


def test_done_patch_does_not_release_pending_full_baseline(studio):
    factory, _, _, _ = studio
    with factory() as db:
        add_take(db, "full", "full", 2, status="queued")
        add_take(db, "patch", "patch", 3, (True, True))
        assert narrator_ready_reason(db, db.get(ScriptChapter, "assembly-chapter")) == "narrator_asr_pending"


def test_submission_identity_breaks_timestamp_ties_before_completion_identity(studio):
    factory, _, _, _ = studio
    with factory() as db:
        earlier = add_take(db, "z-file", "full", 2)
        later = add_take(db, "a-file", "full", 2)
        earlier.correction_source_id, later.correction_source_id = "a-submission", "z-submission"
        db.commit()
        assert [item.id for item in chapter_takes(db, db.get(ScriptChapter, "assembly-chapter"))] == ["a-file"]


@pytest.mark.parametrize("filename", ["Sample_Chapter1-2_Narrator_fix.wav", "Sample_Chapter1_Chapter2_Narrator_fix.wav", "Sample_Narrator_fix.wav", "Sample_Chapter1_Narrator_whole_fix.wav"])
def test_full_requires_exactly_one_named_chapter(studio, filename):
    factory, _, _, _ = studio
    with factory() as db:
        with pytest.raises(ValueError, match="requires_named_chapter"):
            resolve_mode(db, book_code="SB", role=NARRATOR_DISPLAY_NAME, chapter="Chapter 1", filename=filename)


@pytest.mark.parametrize("state", ["missing", "draft", "duplicate"])
def test_full_requires_an_unambiguous_published_chapter(studio, state):
    factory, _, _, _ = studio
    with factory() as db:
        chapter = db.get(ScriptChapter, "assembly-chapter")
        if state == "missing":
            db.delete(chapter)
        elif state == "draft":
            chapter.status = "draft"
        else:
            db.add(ScriptChapter(id="duplicate", book_id="assembly-book", chapter_index=2, chapter_title="Chapter 1", status="published"))
        db.commit()
        with pytest.raises(ValueError, match="requires_published_chapter"):
            resolve_mode(db, book_code="SB", role=NARRATOR_DISPLAY_NAME, chapter="Chapter 1", filename="Sample_Chapter1_Narrator_fix.wav")


LIST_CHAPTER_SCOPES = [
    "Chapter1-2", "Chapter1+2", "Chapter1,2", "Chapter1;2", "Chapter1&2",
    "Chapter1_2", "Chapter1 2", "Chapter1_and_2", "Chapter1 и 2", "Ch01,02", "C№1&2",
]


@pytest.mark.parametrize("scope", LIST_CHAPTER_SCOPES)
def test_full_rejects_chapter_ranges_and_lists(studio, scope):
    factory, _, _, _ = studio
    with factory() as db:
        with pytest.raises(ValueError, match="requires_named_chapter"):
            resolve_mode(db, book_code="SB", role=NARRATOR_DISPLAY_NAME, chapter="Chapter 1",
                         filename=f"Sample_{scope}_Narrator_fix.wav", mode="full")


@pytest.mark.parametrize("filename", [
    "Sample2_Session05_Chapter1_Narrator_fix2.wav",
    "Sample_Chapter1_Narrator_take_2_fix.wav",
    "Sample_Chapter1_Actor7_fix.wav",
])
def test_full_keeps_numbers_in_other_filename_fields(studio, filename):
    factory, _, _, _ = studio
    with factory() as db:
        assert resolve_mode(db, book_code="SB", role=NARRATOR_DISPLAY_NAME, chapter="Chapter 1",
                            filename=filename, mode="full") == "full"


@pytest.mark.parametrize("scope", LIST_CHAPTER_SCOPES)
@pytest.mark.parametrize("route", ["user", "agent"])
def test_bot_full_lists_stop_before_claim_queue_or_paid_work(studio, monkeypatch, scope, route):
    from app.services import asr_run, bot_agent_takes, bot_takes, studio_settings, take_split
    factory, _, _, _ = studio
    with factory() as db:
        book = db.get(ScriptBook, "assembly-book")
        character = Character(id="system", book_id=book.id, name=NARRATOR_DISPLAY_NAME, actor_name="Sample Actor")
        db.add(character)
        db.flush()
        now = db.get(AudioFile, "assembly-narrator").uploaded_at
        upload = BotUpload(id="invalid-full", telegram_user_id="1", file_id="test", kind="document",
            purpose="take", state="choose", book_id=book.id, character_id=character.id, role_name=character.name,
            file_name=f"Sample_{scope}_Narrator_fix.wav", created_at=now)
        db.add(upload)
        db.commit()
        def forbidden(*args, **kwargs):
            pytest.fail("Неоднозначный полный файл дошёл до захвата, очереди или платной работы")
        monkeypatch.setattr(bot_takes, "_claim", forbidden)
        monkeypatch.setattr(bot_takes, "enqueue_take", forbidden)
        monkeypatch.setattr(take_split, "transcribe_whole", forbidden)
        monkeypatch.setattr(asr_run, "run_asr_for_take", forbidden)
        monkeypatch.setattr(studio_settings, "active_book", lambda db: book)
        monkeypatch.setattr(bot_agent_takes, "active_book", lambda db: book)
        monkeypatch.setattr(bot_takes, "my_roles", lambda db, **kwargs: [character])
        monkeypatch.setattr(bot_agent_takes, "_roles", lambda db, book: [character])
        if route == "user":
            out = bot_takes.press(db, tid="1", chat_id=1, data=f"bt:f:{upload.id}:{character.id}", user=None, now=now)
        else:
            out = bot_agent_takes.press(db, tid="1", chat_id=1, data=f"at:f:{upload.id}:{character.id}", now=now)
        assert any("published chapter" in item.payload.get("text", "") for item in out)
        assert not any(item.method.startswith("enqueue") for item in out)
        db.refresh(upload)
        assert upload.state == "choose" and upload.correction_mode == ""
