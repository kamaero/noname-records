"""Персонаж Narrator не отдаёт рассказчику текст, деньги или записанный файл."""
import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services.asr_audit import latest_narrator_take, narrator_lines
from app.services.asr_run import expected_lines
from app.services.role_identity import NARRATOR_REF, book_roles, legacy_language_conflicts
from app.v2.cast_ops import book_cast, create_role
from app.v2.models import V2Attribution, V2Segment
from app.v2.reader import build_chapter_payload
from app.v2.role_export import build_role_export
from app.v2.role_view import role_script
from app.v2.store import store_attributions

NARRATION = "The visitor closed the window."
DIALOGUE = '“I call myself Narrator,” said the visitor.'


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ScriptBook(id="book", title="Silver Window", source_filename="window.txt", source_format="txt",
                          language="en", role_identity_mode="typed_v1", status="published_to_dictor"))
        db.add(ScriptChapter(id="chapter", book_id="book", chapter_index=1, chapter_title="Chapter 1", status="published"))
        db.add(Character(id="system", book_id="book", name="Рассказчик", actor_name="System Actor"))
        db.add(Character(id="person", book_id="book", name="Narrator", actor_name="Character Actor"))
        for ordinal, (speaker, paragraph) in enumerate((("Рассказчик", NARRATION), ("Narrator", DIALOGUE))):
            segment_id = f"seg-{ordinal}"
            db.add(V2Segment(id=segment_id, book_id="book", chapter_id="chapter", ordinal=ordinal, text=paragraph))
        db.flush()
        store_attributions(db, [{"unit_id": "seg-0", "span_start": 0, "span_end": len(NARRATION), "speaker": "Рассказчик"},
                                {"unit_id": "seg-1", "span_start": 0, "span_end": len(DIALOGUE), "speaker": "Narrator"}])
        db.commit()
        yield db
    engine.dispose()


def _audio(db, *, id, role, ref, offset=0):
    audio = AudioFile(id=id, book_code="SW", chapter="Chapter 1", role=role, role_ref=ref, role_book_id="book", actor_name=id,
                      original_filename=f"{id}.wav", canonical_filename=f"{id}.wav", stored_key=f"{id}.wav",
                      mime_type="audio/wav", size_bytes=20, duration_seconds=3,
                      uploaded_at=datetime(2026, 1, 1) + timedelta(seconds=offset))
    db.add(audio)
    db.flush()
    return audio


def test_cast_and_reader_keep_both_roles_and_persist_distinct_refs(db):
    assert {(row.speaker, row.role_ref) for row in db.query(V2Attribution)} == {
        ("Рассказчик", NARRATOR_REF), ("Narrator", "character:person")}
    rows = {row["name"]: row for row in book_cast(db, "book")}
    assert rows["Рассказчик"]["is_narrator"] is True
    assert rows["Narrator"]["is_narrator"] is False
    assert rows["Narrator"]["actor_name"] == "Character Actor"
    assert rows["Narrator"]["lines_count"] == rows["Рассказчик"]["lines_count"] == 1
    payload = build_chapter_payload(db, "chapter", can_edit=True)
    assert [(s["spans"][0]["speaker"], s["spans"][0]["role_ref"]) for s in payload["segments"]] == [
        ("Рассказчик", NARRATOR_REF), ("Narrator", "character:person")]


def test_role_scripts_and_exports_do_not_mix_narration_or_actors(db):
    for role, paragraph, actor in (("Narrator", DIALOGUE, "Character Actor"), ("Рассказчик", NARRATION, "System Actor")):
        script = role_script(db, "book", role, radius=0)
        assert script["counts"]["lines"] == 1
        assert [s["text"] for c in script["chapters"] for s in c["segments"]] == [paragraph]
        exported = build_role_export(db, "book", role)
        assert exported.actor == actor
        assert len(exported.chapters) == 1
        assert len(exported.chapters[0].lines) == 1
        assert paragraph in exported.chapters[0].lines[0].pieces[0].text


def test_asr_and_paid_narrator_audit_select_only_the_system_role(db):
    assert expected_lines(db, "chapter", "Рассказчик") == [NARRATION]
    lines = expected_lines(db, "chapter", "Narrator")
    assert len(lines) == 1 and "I call myself Narrator" in lines[0] and NARRATION not in lines
    assert [line["text"] for line in narrator_lines(db, "chapter")["required"]] == [NARRATION]
    system = _audio(db, id="system-take", role="Рассказчик", ref=NARRATOR_REF)
    _audio(db, id="character-take", role="Narrator", ref="character:person", offset=10)
    assert latest_narrator_take(db, db.get(ScriptChapter, "chapter")).id == system.id


def test_audio_upload_fixes_reference_before_later_character_rename(db, monkeypatch, tmp_path):
    from app.services import audio_uploads as au
    monkeypatch.setattr(au.audio_storage, "local_root", lambda: tmp_path)
    monkeypatch.setattr(au.audio_storage, "free_bytes", lambda root: 10**12)
    monkeypatch.setattr(au.audio_storage, "file_exists", lambda key: False)
    monkeypatch.setattr(au.audio_storage, "write_file", lambda key, payload: "0" * 32)
    monkeypatch.setattr(au.audio_storage, "resolve_path", lambda key, **kw: tmp_path / key)
    monkeypatch.setattr(au.audio_mirror, "ensure_room", lambda *a, **kw: None)
    monkeypatch.setattr(au, "probe_audio_file", lambda path: {"duration_seconds": 3})
    audio = au.store_audio_file(db, payload=b"synthetic", mime_type="audio/wav", original_filename="visitor.wav",
                               book_code="SW", chapter="Chapter 1", role="Narrator", actor_name="Character Actor",
                               safe_name=lambda s: s)
    assert audio.role_ref == "character:person"
    db.get(Character, "person").name = "The Visitor"
    db.flush()
    identities = book_roles(db, "book")
    assert identities.name(audio.role_ref, audio.role) == "The Visitor"
    payload = build_chapter_payload(db, "chapter")
    assert payload["segments"][1]["spans"][0]["speaker"] == "The Visitor"
    assert payload["segments"][1]["spans"][0]["role_ref"] == audio.role_ref


def test_typed_role_creation_accepts_literal_narrator_but_legacy_does_not(db):
    db.add(ScriptBook(id="fresh", title="Fresh", source_filename="f.txt", source_format="txt",
                      language="en", role_identity_mode="typed_v1"))
    db.add(ScriptBook(id="old", title="Old", source_filename="o.txt", source_format="txt", language="ru"))
    db.flush()
    assert create_role(db, book_id="fresh", name="Narrator", actor_uid="editor")["created"]
    with pytest.raises(ValueError, match="narrator_is_not_a_role"):
        create_role(db, book_id="old", name="Narrator", actor_uid="editor")
    assert book_roles(db, "old").is_narrator("Narrator")
    assert not book_roles(db, "fresh").is_narrator("Narrator")


def test_legacy_language_preflight_reports_aliases_from_recordings_without_mutation(db):
    book = db.get(ScriptBook, "book")
    book.role_identity_mode, book.language = "legacy", "ru"
    db.flush()
    before = [(r.id, r.speaker, r.role_ref) for r in db.query(V2Attribution)]
    assert legacy_language_conflicts(db, book) == ["Narrator"]
    assert book.language == "ru" and book.role_identity_mode == "legacy"
    assert [(r.id, r.speaker, r.role_ref) for r in db.query(V2Attribution)] == before


def test_migration_keeps_even_existing_english_books_legacy_and_is_idempotent():
    spec = importlib.util.spec_from_file_location("typed_role_migration", Path(__file__).parents[1] / "alembic/versions/0063_typed_role_identity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE script_books (id TEXT PRIMARY KEY, language TEXT)"))
        conn.execute(text("CREATE TABLE v2_attributions (id TEXT PRIMARY KEY, speaker TEXT)"))
        conn.execute(text("CREATE TABLE audio_files (id TEXT PRIMARY KEY, role TEXT)"))
        conn.execute(text("CREATE TABLE asr_jobs (id TEXT PRIMARY KEY, expected_role TEXT)"))
        conn.execute(text("INSERT INTO script_books VALUES ('old-en', 'en')"))
        conn.execute(text("INSERT INTO v2_attributions VALUES ('a', 'Narrator')"))
        module.op = Operations(MigrationContext.configure(conn))
        module.upgrade()
        module.upgrade()
        assert conn.execute(text("SELECT role_identity_mode FROM script_books")).scalar_one() == "legacy"
        assert conn.execute(text("SELECT speaker, role_ref FROM v2_attributions")).one() == ("Narrator", "")
        conn.execute(text("UPDATE script_books SET role_identity_mode='typed_v1'"))
        with pytest.raises(RuntimeError, match="typed_role_identity_in_use"):
            module.downgrade()


def test_only_fresh_english_imports_enable_typed_identity(db, monkeypatch):
    from app.services import book_import
    monkeypatch.setattr(book_import, "_persist_book_source_snapshot", lambda *a: None)
    for language, mode in (("en", "typed_v1"), ("ru", "legacy")):
        book = book_import.enqueue_book(db, "sample.txt", b"A small sample paragraph.", language=language)
        assert book.role_identity_mode == mode


def test_unicode_names_have_distinct_refs_even_when_legacy_safe_name_collapses_them(db):
    from app.services.shared_runtime import safe_name
    assert safe_name("Caf\u00e9") == safe_name("Caf")
    db.add_all([Character(id="unicode-role", book_id="book", name="Caf\u00e9"),
                Character(id="ascii-role", book_id="book", name="Caf")])
    db.flush()
    identities = book_roles(db, "book")
    assert identities.ref("Caf\u00e9") == "character:unicode-role"
    assert identities.ref("Caf") == "character:ascii-role"


def test_language_api_blocks_legacy_ambiguity_before_any_changes_or_paid_work(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from app.auth import session_serializer
    from app.main import app
    from app.models import ScriptLog
    engine = create_engine(f"sqlite:///{tmp_path / 'ambiguous.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.api.SessionLocal", factory)
    monkeypatch.setattr("app.workers.launcher.enqueue_tracked_task", lambda *a, **kw: pytest.fail("paid work must not start"))
    with factory() as session:
        session.add(ScriptBook(id="old", title="Old", source_filename="o.txt", source_format="txt", language="ru"))
        session.add(Character(id="alias", book_id="old", name="Narrator"))
        session.commit()
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "editor", "sub": "Test Editor", "roles": ["admin"]}))
    response = client.post("/api/v2/books/old/language", json={"language": "en"})
    assert response.status_code == 409
    assert response.json()["error"] == "ambiguous_narrator_identity"
    assert response.json()["roles"] == ["Narrator"]
    with factory() as session:
        book = session.get(ScriptBook, "old")
        assert (book.language, book.role_identity_mode) == ("ru", "legacy")
        assert session.query(ScriptLog).count() == 0
    engine.dispose()


def test_renamed_character_audio_still_assembles_into_its_own_session_track(db, monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET
    from app.services import chapter_delivery, audio_storage
    _audio(db, id="system-take", role="Рассказчик", ref=NARRATOR_REF)
    _audio(db, id="character-take", role="Narrator", ref="character:person")
    db.get(Character, "person").name = "The Visitor"
    db.flush()
    status = chapter_delivery.chapter_recording_status(db, "chapter")
    assert status["recorded_roles"] == status["total_roles"] == 2
    rows = {row["role"]: row for row in status["roles"]}
    assert [f["id"] for f in rows["Рассказчик"]["takes"]] == ["system-take"]
    assert [f["id"] for f in rows["The Visitor"]["takes"]] == ["character-take"]
    exported = build_role_export(db, "book", "The Visitor")
    assert len(exported.chapters[0].lines) == 1
    monkeypatch.setattr(audio_storage, "resolve_path", lambda key, **kw: tmp_path / key)
    xml = chapter_delivery.build_chapter_session(db, "chapter", include_audit=False)
    root = ET.fromstring(xml.lstrip("\ufeff"))
    names = [node.find("trackParameters/name").text for node in root.findall("session/tracks/audioTrack")]
    assert "Рассказчик" in names and "The Visitor" in names
    assert "Narrator" not in names


def test_typed_budget_and_payouts_keep_character_fee_separate_from_narrator(db):
    from app.models import BookBudget, CharacterBudgetSnapshot
    from app.services.payouts import role_fees
    from app.v2.budget_ops import rebuild_v2_budget
    db.get(Character, "person").manual_fixed_rub = 1200
    db.get(Character, "system").manual_fixed_rub = 9000
    db.add(BookBudget(book_id="book", narrator_actor_name="System Actor", narrative_cost_rub=9000))
    rebuild_v2_budget(db, "book")
    snapshots = {row.character_id: row for row in db.query(CharacterBudgetSnapshot)}
    assert snapshots["person"].lines_count == snapshots["system"].lines_count == 1
    assert snapshots["person"].total_rub == 1200
    fees = {row["role_key"]: row for row in role_fees(db, db.get(ScriptBook, "book"))}
    assert fees["person"]["role_name"] == "Narrator"
    assert fees["person"]["fee"] == 1200
    assert fees["narrator"]["fee"] == 9000


def test_paid_asr_job_snapshots_role_ref_and_replay_uses_it_after_rename(db, monkeypatch, tmp_path):
    from app.services import asr_run, asr_replay, audio_storage
    from app.models import AsrJob
    audio = _audio(db, id="character-take", role="Narrator", ref="character:person")
    db.get(Character, "person").name = "The Visitor"
    db.flush()
    monkeypatch.setattr(audio_storage, "resolve_path", lambda key, **kw: tmp_path / key)
    monkeypatch.setattr("app.services.step_models.step_model", lambda step: ("openai", "test-asr"))
    heard = {"text": "I call myself Narrator", "segments": [{"text": "I call myself Narrator", "start": 0, "end": 2}]}
    job = asr_run.run_asr_for_take(db, audio.id, transcribe=lambda path: heard, notify=False)
    assert job.status == "done"
    assert (job.role_ref, job.expected_role, job.language) == ("character:person", "The Visitor", "en")
    db.flush()
    old_transcript = job.heard_json
    report = asr_replay.realign_job(db, job.id)
    assert json.loads(job.alignment_json)["total"] == 1
    assert db.get(AsrJob, job.id).role_ref == "character:person"
    assert job.heard_json == old_transcript


def test_text_and_pdf_export_localize_only_the_explicit_system_role_and_cue(db):
    import io
    from pypdf import PdfReader
    from app.v2.role_export import render_text
    from app.v2.role_export_pdf import render_pdf
    character = build_role_export(db, "book", "Narrator")
    system = build_role_export(db, "book", "Рассказчик")
    assert system.role == "Рассказчик" and system.role_ref == NARRATOR_REF
    assert character.role == "Narrator" and character.role_ref == "character:person"
    assert character.chapters[0].lines[0].cue_speaker == "Рассказчик"
    assert character.chapters[0].lines[0].cue_role_kind == "system_narrator"
    txt = render_text(character)
    assert "Narrator (system): " + NARRATION in txt
    assert "▶ Narrator: " in txt
    for exported in (character, system):
        rendered = render_text(exported)
        assert "▶ Narrator (system): " in rendered if exported is system else "▶ Narrator: " in rendered
        doc = PdfReader(io.BytesIO(render_pdf(exported)))
        rendered_pdf = "\n".join(page.extract_text() or "" for page in doc.pages)
        assert "Narrator (system)" in rendered_pdf
        assert exported.role == ("Рассказчик" if exported is system else "Narrator")


def test_ambiguous_typed_audio_book_code_fails_before_disk_or_database_write(db, monkeypatch):
    from app.services import audio_uploads as au
    db.add(ScriptBook(id="collision", title="Silver Wings", source_filename="w.txt", source_format="txt"))
    db.flush()
    monkeypatch.setattr(au.audio_storage, "write_file", lambda *a: pytest.fail("no disk write before identity resolution"))
    with pytest.raises(ValueError, match="ambiguous_book_code"):
        au.store_audio_file(db, payload=b"synthetic", mime_type="audio/wav", original_filename="visitor.wav",
                            book_code="SW", chapter="Chapter 1", role="Narrator", actor_name="Character Actor",
                            safe_name=lambda s: s)
    assert db.query(AudioFile).count() == 0


@pytest.mark.parametrize("collision", ["canonical", "alias"])
def test_ambiguous_labels_fail_closed_before_attribution_upload_asr_or_export(db, monkeypatch, collision):
    from app.services.role_identity import RoleIdentityError
    from app.services import audio_uploads, asr_run
    db.add(Character(id="collision", book_id="book", name="NARRATOR" if collision == "canonical" else "Another",
                     aliases="" if collision == "canonical" else "Narrator"))
    db.flush()
    with pytest.raises(RoleIdentityError):
        book_roles(db, "book").ref("Narrator")
    before = db.query(V2Attribution).count()
    with pytest.raises(RoleIdentityError):
        store_attributions(db, [{"unit_id": "seg-0", "span_start": 0, "span_end": 2, "speaker": "Рассказчик"},
                                {"unit_id": "seg-1", "span_start": 0, "span_end": 2, "speaker": "Narrator"}])
    assert db.query(V2Attribution).count() == before
    monkeypatch.setattr(audio_uploads.audio_storage, "write_file", lambda *a: pytest.fail("must not write"))
    with pytest.raises(RoleIdentityError):
        audio_uploads.store_audio_file(db, payload=b"synthetic", mime_type="audio/wav", original_filename="voice.wav",
                                      book_code="SW", chapter="Chapter 1", role="Narrator", actor_name="Actor", safe_name=lambda s: s)
    audio = _audio(db, id="ambiguous", role="Narrator", ref="character:person")
    with pytest.raises(RoleIdentityError):
        asr_run.run_asr_for_take(db, audio.id, transcribe=lambda *a: pytest.fail("must not spend"), notify=False)
    with pytest.raises(RoleIdentityError):
        build_role_export(db, "book", "Narrator")


def test_typed_audio_owner_prevents_same_code_cross_book_reuse(db):
    from app.services.asr_run import find_chapter_for_take
    from app.services.chapter_delivery import chapter_recording_status
    db.add(ScriptBook(id="other", title="Silver Wings", source_filename="other.txt", source_format="txt", role_identity_mode="typed_v1"))
    db.add(ScriptChapter(id="other-chapter", book_id="other", chapter_index=1, chapter_title="Chapter 1"))
    db.flush()
    audio = _audio(db, id="owned", role="Рассказчик", ref=NARRATOR_REF)
    assert find_chapter_for_take(db, audio).id == "chapter"
    assert latest_narrator_take(db, db.get(ScriptChapter, "chapter")).id == "owned"
    assert latest_narrator_take(db, db.get(ScriptChapter, "other-chapter")) is None
    assert chapter_recording_status(db, "other-chapter")["orphan_takes"] == []


def test_authenticated_ambiguous_role_download_returns_scoped_409(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from app.auth import session_serializer
    from app.main import app
    engine = create_engine(f"sqlite:///{tmp_path / 'cast.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.api.SessionLocal", factory)
    with factory() as session:
        session.add(ScriptBook(id="book", title="Synthetic", source_filename="a.txt", source_format="txt", role_identity_mode="typed_v1"))
        session.add_all([Character(id="one", book_id="book", name="Narrator"), Character(id="two", book_id="book", name="Other", aliases="Narrator")])
        session.commit()
    client = TestClient(app)
    client.cookies.set("session", session_serializer.dumps({"uid": "editor", "sub": "Test Editor", "roles": ["admin"]}))
    for path in ["role-script", "role-script.txt", "role-script.pdf"]:
        response = client.get(f"/api/v2/books/book/{path}", params={"role": "Narrator"})
        assert response.status_code == 409
        assert response.json()["error"] == "ambiguous_role_identity"
        assert "Other" not in response.text
    engine.dispose()


@pytest.mark.parametrize("mode", ["legacy", "typed_v1"])
def test_asr_table_sixty_chapters_has_bounded_queries_and_current_audits(db, mode):
    from time import perf_counter
    from sqlalchemy import event
    from app.models import AsrAudit, AsrJob
    from app.services.asr_audit import AUDIT_POLICY_VERSION, narrator_input_fingerprint
    from app.services.chapter_delivery import book_recording_status
    book = db.get(ScriptBook, "book")
    book.role_identity_mode = mode
    chapter_ids = []
    for index in range(60):
        cid, sid, aid = f"perf-{index}", f"perf-seg-{index}", f"perf-audio-{index}"
        label, paragraph = f"Chapter {index + 2}", "The window opened slowly."
        chapter_ids.append(cid)
        db.add(ScriptChapter(id=cid, book_id="book", chapter_index=index + 2, chapter_title=label, status="published"))
        db.add(V2Segment(id=sid, book_id="book", chapter_id=cid, ordinal=0, text=paragraph))
        db.add(V2Attribution(id=f"perf-attr-{index}", segment_id=sid, span_start=0, span_end=len(paragraph),
                             speaker="Рассказчик", role_ref=NARRATOR_REF if mode == "typed_v1" else "", version=1))
        db.add(AudioFile(id=aid, book_code="SW", chapter=label, role="Рассказчик", role_ref=NARRATOR_REF if mode == "typed_v1" else "",
                         role_book_id="book" if mode == "typed_v1" else "", kind="take", original_filename="synthetic.wav",
                         canonical_filename="synthetic.wav", stored_key=f"{aid}.wav", mime_type="audio/wav", size_bytes=1))
        db.add(AsrJob(audio_file_id=aid, chapter_id=cid, status="done", alignment_json=json.dumps({
            "lines": [{"index": 0, "text": paragraph, "matched": True, "start": 0., "end": 2.}]})))
    db.flush()
    for cid in chapter_ids:
        db.add(AsrAudit(chapter_id=cid, status="done", lines_json=json.dumps({
            "audit_policy_version": AUDIT_POLICY_VERSION,
            "narrator_input_fingerprint": narrator_input_fingerprint(db, cid)})))
    db.commit()
    db.expire_all()
    queries = []
    def count(*args):
        queries.append(1)
    event.listen(db.get_bind(), "before_cursor_execute", count)
    started = perf_counter()
    try:
        status = book_recording_status(db, "book")
    finally:
        elapsed = perf_counter() - started
        event.remove(db.get_bind(), "before_cursor_execute", count)
    assert len(status["chapters"]) == 61
    current = {r["chapter_id"]: r for r in status["chapters"]}
    assert all(current[cid]["audit"]["status"] == "done" for cid in chapter_ids)
    assert len(queries) <= 15
    report_path = Path("/tmp/noname-typed-asr-performance.json")
    report = json.loads(report_path.read_text()) if report_path.exists() else {}
    report[mode] = {"chapters": 61, "audits": 60, "sql_queries": len(queries), "elapsed_seconds": elapsed}
    report_path.write_text(json.dumps(report, indent=2))


def test_deleted_role_ref_cannot_be_reassigned_to_new_character_with_same_name(db, monkeypatch):
    from app.services import asr_run
    from app.services.role_identity import RoleIdentityError
    from app.services.chapter_delivery import chapter_recording_status
    audio = _audio(db, id="old-character", role="Narrator", ref="character:person")
    db.delete(db.get(Character, "person"))
    db.add(Character(id="replacement", book_id="book", name="Narrator"))
    db.flush()
    with pytest.raises(RoleIdentityError, match="unknown_role_identity"):
        build_role_export(db, "book", "Narrator")
    # Удалённая разметка не скрывает оставшийся файл и не отдаёт его новой роли.
    db.query(V2Attribution).filter(V2Attribution.role_ref == "character:person").delete(synchronize_session=False)
    db.flush()
    status = chapter_recording_status(db, "chapter")
    assert any(take["id"] == audio.id for take in status["orphan_takes"])
    with pytest.raises(RoleIdentityError, match="unknown_role_identity"):
        asr_run.run_asr_for_take(db, audio.id, transcribe=lambda *a: pytest.fail("must not spend"), notify=False)


def test_unicode_storage_collision_keeps_both_payloads_and_role_refs(db, monkeypatch, tmp_path):
    from app.services import audio_uploads as au
    from app.services.shared_runtime import safe_name
    db.add_all([Character(id="unicode", book_id="book", name="Café"), Character(id="ascii", book_id="book", name="Caf")])
    db.flush()
    written = {}
    monkeypatch.setattr(au.audio_storage, "local_root", lambda: tmp_path)
    monkeypatch.setattr(au.audio_storage, "free_bytes", lambda root: 10**12)
    monkeypatch.setattr(au.audio_storage, "file_exists", lambda key: key in written)
    def write(key, payload):
        assert key not in written
        written[key] = payload
        return "0" * 32
    monkeypatch.setattr(au.audio_storage, "write_file", write)
    monkeypatch.setattr(au.audio_storage, "resolve_path", lambda key, **kw: tmp_path / key)
    monkeypatch.setattr(au.audio_mirror, "ensure_room", lambda *a, **kw: None)
    monkeypatch.setattr(au, "probe_audio_file", lambda path: {"duration_seconds": 3})
    first, second = [au.store_audio_file(db, payload=payload, mime_type="audio/wav", original_filename="voice.wav",
                                       book_code="SW", chapter="Chapter 1", role=role, actor_name="Actor", safe_name=safe_name)
                     for role, payload in [("Café", b"first"), ("Caf", b"second")]]
    assert first.role_ref == "character:unicode" and second.role_ref == "character:ascii"
    assert first.stored_key != second.stored_key
    assert written[first.stored_key] == b"first" and written[second.stored_key] == b"second"
