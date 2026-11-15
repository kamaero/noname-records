"""Скачивание приватного дубля требует свежего назначения и неизменного источника."""
import hashlib
import io
import json
import wave
import zipfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import session_serializer
from app.db import Base
from app.main import app
from app.models import AsrAudit, AsrJob, AudioFile, Character, ScriptBook, ScriptChapter, User, UserRole
from app.services import asr_audit, narrator_audit_export as export
from app.v2.models import V2Attribution, V2Segment

CHAPTER = "chapter-export"


def _login(client, uid="actor", roles=None):
    client.cookies.set("session", session_serializer.dumps({"uid": uid, "sub": uid,
        "display_name": "Spoofed cookie name", "roles": roles or []}))


@pytest.fixture
def studio(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.v2.narrator_audit_api.SessionLocal", factory)
    monkeypatch.setattr(export.audio_storage, "local_root", lambda: str(tmp_path))
    # Искусственный звук полностью создаётся тестом; записи студии не читаются.
    wav = io.BytesIO()
    with wave.open(wav, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(100)
        audio.writeframes(b"\0\0" * 2000)
    content = wav.getvalue()
    (tmp_path / "take.wav").write_bytes(content)
    with factory() as db:
        db.add(ScriptBook(id="book-export", title="Проба", source_filename="x.txt", source_format="txt"))
        db.add(ScriptChapter(id=CHAPTER, book_id="book-export", chapter_index=1, chapter_title="Глава 1", status="published"))
        db.add(Character(id="narrator", book_id="book-export", name="Рассказчик", actor_name="Sample Actor"))
        for uid, name in [("actor", "Sample Actor"), ("other", "Sample"), ("editor", "Editor")]:
            db.add(User(id=uid, login=uid, password_hash="unused", display_name=name))
        db.add(UserRole(user_id="editor", role="author"))
        text = "Впереди сиял красный огонь."
        db.add(V2Segment(id="segment-export", chapter_id=CHAPTER, book_id="book-export", ordinal=0,
                         kind="paragraph", text=text))
        db.add(V2Attribution(id="attribution-export", segment_id="segment-export", version=1,
                             speaker="Рассказчик", span_start=0, span_end=len(text), source="llm"))
        db.add(AudioFile(id="take-export", role_book_id="book-export", book_code="П",
                         chapter="Глава 1", role="Рассказчик", actor_name="Sample Actor", kind="take",
                         mime_type="audio/wav", stored_key="take.wav", original_filename="take.wav",
                         canonical_filename="take.wav", size_bytes=len(content), duration_seconds=20,
                         md5=hashlib.md5(content).hexdigest()))
        db.add(AsrJob(audio_file_id="take-export", chapter_id=CHAPTER, status="done",
                      alignment_json=json.dumps({"lines": [{"matched": True, "start": 5, "end": 10}]})))
        db.commit()
        fingerprint = asr_audit.narrator_input_fingerprint(db, CHAPTER)
        flag = {"index": 0, "segment_id": "segment-export", "text": text, "confirmed": True,
                "missing_words": ["красный"], "note": "Проверьте\tсоседние\nслова.", "confirm_note": "Подтверждено",
                "source_windows": [{"source_audio_file_id": "take-export", "start": 5, "end": 10}],
                "word_anchors": [{"source_audio_file_id": "take-export", "start": 7, "end": 7.3,
                                  "word": "красный", "approximate": True}]}
        db.add(AsrAudit(id="audit-export", chapter_id=CHAPTER, audio_file_id="take-export", status="done",
                        lines_json=json.dumps({"audit_policy_version": asr_audit.AUDIT_POLICY_VERSION,
                                               "narrator_input_fingerprint": fingerprint}),
                        findings_json=json.dumps({"flags": [flag]})))
        db.commit()
    client = TestClient(app)
    _login(client)
    yield client, factory, tmp_path, content


def _path():
    return f"/api/v2/chapters/{CHAPTER}/narrator-audit"


def _change_flag(factory, change):
    with factory() as db:
        audit = db.get(AsrAudit, "audit-export")
        payload = json.loads(audit.findings_json)
        change(payload["flags"][0])
        audit.findings_json = json.dumps(payload)
        db.commit()


def test_actor_can_download_byte_identical_wav_with_importable_labels(studio):
    client, factory, directory, content = studio
    response = client.get(_path())
    assert response.status_code == 200
    payload = response.json()
    assert payload["audit_id"] == "audit-export"
    assert len(payload["items"]) == 1
    assert payload["items"][0]["findings"][0]["markers"][0]["approximate"] is True
    assert "stored_key" not in response.text and "transcript" not in response.text
    download = client.get(payload["items"][0]["download_url"])
    assert download.status_code == 200 and download.headers["content-type"] == "application/zip"
    assert download.headers["cache-control"] == "private, no-store"
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.namelist() == ["source.wav", "labels.txt", "findings.txt", "README.txt"]
        assert archive.read("source.wav") == content
        labels = archive.read("labels.txt").decode("utf-8").splitlines()
        assert len(labels) == 1
        start, end, text = labels[0].split("\t")
        assert (float(start), float(end)) == (7, 7.3)
        assert "APPROXIMATE" in text and "красный" in text and "Впереди" in text
        assert "File > Import > Labels" in archive.read("README.txt").decode("utf-8")
    assert (directory / "take.wav").read_bytes() == content
    assert sorted(path.name for path in directory.iterdir()) == ["take.wav"]


@pytest.mark.parametrize("uid,expected", [(None, 401), ("other", 403), ("editor", 200), ("missing", 403)])
def test_endpoint_uses_current_identity_and_db_permissions(studio, uid, expected):
    client, factory, _, _ = studio
    url = client.get(_path()).json()["items"][0]["download_url"]
    client.cookies.clear()
    if uid:
        _login(client, uid, roles=["admin"])
    assert client.get(_path()).status_code == expected
    assert client.get(url).status_code == expected


def test_unpublished_chapter_is_hidden_from_actor_even_with_old_download_link(studio):
    client, factory, _, _ = studio
    url = client.get(_path()).json()["items"][0]["download_url"]
    with factory() as db:
        db.get(ScriptChapter, CHAPTER).status = "draft"
        db.commit()
    assert client.get(_path()).status_code == 404
    assert client.get(url).status_code == 404
    _login(client, "editor")
    assert client.get(_path()).status_code == 200


@pytest.mark.parametrize("change", ["reassigned", "tentative", "duplicate", "inactive", "source_owner"])
def test_exact_assignment_and_source_owner_are_required(studio, change):
    client, factory, _, _ = studio
    url = client.get(_path()).json()["items"][0]["download_url"]
    with factory() as db:
        if change == "reassigned":
            db.get(Character, "narrator").actor_name = "Sample"
        elif change == "tentative":
            db.get(Character, "narrator").actor_name = "Sample Actor?"
        elif change == "duplicate":
            db.add(User(id="duplicate", login="duplicate", password_hash="unused", display_name="Sample Actor"))
        elif change == "inactive":
            db.get(User, "actor").is_active = "false"
        else:
            db.get(AudioFile, "take-export").actor_name = "Sample"
        db.commit()
    response = client.get(_path())
    if change == "source_owner":
        assert response.status_code == 200 and response.json()["items"] == []
        assert client.get(url).status_code == 404
    else:
        assert response.status_code == 403 and client.get(url).status_code == 403


def test_stale_audit_or_changed_script_cannot_be_downloaded(studio):
    client, factory, _, _ = studio
    url = client.get(_path()).json()["items"][0]["download_url"]
    with factory() as db:
        db.get(V2Segment, "segment-export").text += " Новая строка."
        db.get(V2Attribution, "attribution-export").span_end += len(" Новая строка.")
        db.commit()
    assert client.get(_path()).json()["items"] == []
    assert client.get(url).status_code == 409


@pytest.mark.parametrize("change", ["nan", "outside", "synthetic", "wrong_address", "unmapped"])
def test_unsafe_timing_keeps_context_without_a_false_label(studio, change):
    client, factory, _, _ = studio
    def mutate(flag):
        anchor = flag["word_anchors"][0]
        if change == "nan":
            anchor["start"] = float("nan")
        elif change == "outside":
            anchor.update(start=2, end=3)
        elif change == "synthetic":
            anchor.pop("source_audio_file_id")
        elif change == "wrong_address":
            anchor["segment_id"] = "other-segment"
        else:
            anchor["mapped"] = False
    _change_flag(factory, mutate)
    payload = client.get(_path()).json()
    assert payload["items"][0]["findings"][0]["marker_unavailable"] is True
    download = client.get(payload["items"][0]["download_url"])
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.read("labels.txt") == b""
        assert "Time unavailable" in archive.read("findings.txt").decode("utf-8")


@pytest.mark.parametrize("change", ["unconfirmed", "wrong_text", "wrong_index", "other_source"])
def test_invalid_finding_is_not_exposed(studio, change):
    client, factory, _, _ = studio
    def mutate(flag):
        if change == "unconfirmed":
            flag["confirmed"] = False
        elif change == "wrong_text":
            flag["text"] = "Чужая строка"
        elif change == "wrong_index":
            flag["index"] = 55
        else:
            flag["source_windows"] = [{"source_audio_file_id": "other-source"}]
            flag["word_anchors"][0]["source_audio_file_id"] = "other-source"
    _change_flag(factory, mutate)
    assert client.get(_path()).json()["items"] == []


@pytest.mark.parametrize("change", ["bytes", "size", "missing_md5", "nonwav", "symlink", "directory", "parent"])
def test_download_rejects_tampered_unsafe_or_unbound_sources(studio, change):
    client, factory, directory, content = studio
    url = client.get(_path()).json()["items"][0]["download_url"]
    if change == "bytes":
        (directory / "take.wav").write_bytes(content[:-1] + b"x")
    elif change == "nonwav":
        bad = b"not-a-wave-file"
        (directory / "take.wav").write_bytes(bad)
        with factory() as db:
            take = db.get(AudioFile, "take-export")
            take.md5, take.size_bytes = hashlib.md5(bad).hexdigest(), len(bad)
            db.commit()
    elif change == "symlink":
        (directory / "other.wav").write_bytes(content)
        (directory / "take.wav").unlink()
        (directory / "take.wav").symlink_to(directory / "other.wav")
    elif change == "directory":
        (directory / "take.wav").unlink()
        (directory / "take.wav").mkdir()
    else:
        with factory() as db:
            take = db.get(AudioFile, "take-export")
            if change == "size":
                take.size_bytes += 1
            elif change == "parent":
                take.stored_key = "../outside.wav"
            else:
                take.md5 = ""
            db.commit()
    response = client.get(url)
    assert response.status_code in {409, 503}
    assert "zip" not in response.headers["content-type"]
    assert "stored_key" not in response.text and str(directory) not in response.text


def test_multifile_chapter_exports_each_source_with_its_own_times(studio):
    client, factory, directory, content = studio
    (directory / "second.wav").write_bytes(content)
    with factory() as db:
        text = "За деревьями начиналась река."
        db.add(V2Segment(id="segment-second", chapter_id=CHAPTER, book_id="book-export", ordinal=1,
                         kind="paragraph", text=text))
        db.add(V2Attribution(id="attribution-second", segment_id="segment-second", version=1,
                             speaker="Рассказчик", span_start=0, span_end=len(text), source="llm"))
        first_job = db.query(AsrJob).filter_by(audio_file_id="take-export").one()
        first_job.alignment_json = json.dumps({"lines": [
            {"matched": True, "start": 5, "end": 10}, {"matched": False}]})
        db.add(AudioFile(id="take-second", role_book_id="book-export", book_code="П",
                         chapter="Глава 1", role="Рассказчик", actor_name="Sample Actor", kind="take",
                         mime_type="audio/wav", stored_key="second.wav", original_filename="second.wav",
                         canonical_filename="second.wav", size_bytes=len(content), duration_seconds=20,
                         md5=hashlib.md5(content).hexdigest()))
        db.add(AsrJob(audio_file_id="take-second", chapter_id=CHAPTER, status="done",
                      alignment_json=json.dumps({"lines": [{"matched": False},
                          {"matched": True, "start": 1, "end": 4}]})))
        db.flush()
        audit = db.get(AsrAudit, "audit-export")
        flags = json.loads(audit.findings_json)["flags"]
        flags.append({"index": 1, "segment_id": "segment-second", "text": text, "confirmed": True,
            "missing_words": ["река"], "source_windows": [{"source_audio_file_id": "take-second"}],
            "word_anchors": [{"source_audio_file_id": "take-second", "start": 2, "end": 2.4, "word": "река"}]})
        audit.findings_json = json.dumps({"flags": flags})
        audit.lines_json = json.dumps({"audit_policy_version": asr_audit.AUDIT_POLICY_VERSION,
                                       "narrator_input_fingerprint": asr_audit.narrator_input_fingerprint(db, CHAPTER)})
        db.commit()
    items = client.get(_path()).json()["items"]
    assert {item["audio_file_id"] for item in items} == {"take-export", "take-second"}
    for item in items:
        download = client.get(item["download_url"])
        assert download.status_code == 200
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            labels = archive.read("labels.txt").decode("utf-8")
            if item["audio_file_id"] == "take-second":
                assert labels.startswith("2.000000\t2.400000\t") and "река" in labels and "красный" not in labels
            else:
                assert labels.startswith("7.000000\t7.300000\t") and "красный" in labels and "река" not in labels


@pytest.mark.parametrize("change,code", [("no_md5", "source_integrity_failed"),
    ("size", "source_integrity_failed"), ("not_wav", "source_not_wav"),
    ("bad_checksum", "source_integrity_failed")])
def test_archive_checks_actual_source_before_exposing_any_bytes(studio, change, code):
    client, factory, directory, content = studio
    with factory() as db:
        payload, sources = export.current_export(db, "actor", CHAPTER)
    source = dict(sources["take-export"])
    if change == "no_md5":
        source["md5"] = ""
    elif change == "size":
        source["size_bytes"] += 1
    elif change == "bad_checksum":
        source["md5"] = "0" * 32
    else:
        (directory / "take.wav").write_bytes(b"not-wav" + content[7:])
        source["md5"] = hashlib.md5((directory / "take.wav").read_bytes()).hexdigest()
    with pytest.raises(export.ExportError) as caught:
        export.build_archive(source, payload["items"][0], audit_id="audit-export", fingerprint=payload["fingerprint"])
    assert caught.value.code == code


def test_successful_new_audit_removes_previous_findings_and_link(studio):
    from datetime import timedelta
    from app.time_utils import utcnow_naive
    client, factory, _, _ = studio
    url = client.get(_path()).json()["items"][0]["download_url"]
    with factory() as db:
        old = db.get(AsrAudit, "audit-export")
        db.add(AsrAudit(id="clean-audit", chapter_id=CHAPTER, status="done", lines_json=old.lines_json,
                        created_at=utcnow_naive() + timedelta(seconds=1), findings_json='{"flags": []}'))
        db.commit()
    payload = client.get(_path()).json()
    assert payload["audit_id"] == "clean-audit" and payload["items"] == []
    assert client.get(url).status_code == 409


def test_unavailable_nas_is_rejected_without_touching_remote_mount(studio, monkeypatch):
    client, factory, _, _ = studio
    with factory() as db:
        payload, sources = export.current_export(db, "actor", CHAPTER)
    source = {**sources["take-export"], "location": "nas"}
    monkeypatch.setattr("app.services.nas_health.nas_online", lambda: False)
    monkeypatch.setattr(export.audio_storage, "nas_root", lambda: pytest.fail("unavailable NAS touched"))
    with pytest.raises(export.ExportError, match="source_unavailable"):
        export.build_archive(source, payload["items"][0], audit_id="audit-export", fingerprint=payload["fingerprint"])


@pytest.mark.parametrize("change", ["rights", "assignment", "text", "finding", "source_metadata"])
def test_long_archive_rechecks_permissions_and_inputs_before_response(studio, monkeypatch, change):
    client, factory, _, _ = studio
    if change == "rights":
        _login(client, "editor")
    url = client.get(_path()).json()["items"][0]["download_url"]
    from app.v2 import narrator_audit_api
    original_build = narrator_audit_api.build_archive
    archives = []
    def build_then_change(*args, **kwargs):
        archive = original_build(*args, **kwargs)
        archives.append(archive)
        with factory() as db:
            if change == "rights":
                db.query(UserRole).filter_by(user_id="editor").delete()
            elif change == "assignment":
                db.get(Character, "narrator").actor_name = "Sample"
            elif change == "text":
                db.get(V2Segment, "segment-export").text = "Совершенно другой текст."
            elif change == "source_metadata":
                db.get(AudioFile, "take-export").verify_state = "mismatch"
            else:
                db.get(AsrAudit, "audit-export").findings_json = '{"flags": []}'
            db.commit()
        return archive
    monkeypatch.setattr(narrator_audit_api, "build_archive", build_then_change)
    response = client.get(url)
    assert response.status_code in {403, 409}
    assert response.headers["content-type"].startswith("application/json")
    assert len(archives) == 1 and archives[0].closed


def test_nested_storage_symlink_cannot_escape_root(studio):
    client, factory, directory, content = studio
    (directory / "alias").symlink_to(directory, target_is_directory=True)
    with factory() as db:
        payload, sources = export.current_export(db, "actor", CHAPTER)
    source = {**sources["take-export"], "stored_key": "alias/take.wav"}
    with pytest.raises(export.ExportError, match="source_unavailable"):
        export.build_archive(source, payload["items"][0], audit_id="audit-export", fingerprint=payload["fingerprint"])


def test_stream_cleanup_closes_temporary_file_on_early_disconnect():
    archive = io.BytesIO(b"x" * (1024 * 1024 + 1))
    chunks = export.archive_chunks(archive)
    assert len(next(chunks)) == 1024 * 1024
    chunks.close()
    assert archive.closed


def test_archive_limit_lasts_until_stream_is_closed(studio):
    client, factory, _, _ = studio
    with factory() as db:
        payload, sources = export.current_export(db, "actor", CHAPTER)
    def build():
        return export.build_archive(sources["take-export"], payload["items"][0],
                                    audit_id="audit-export", fingerprint=payload["fingerprint"])
    archive = build()
    try:
        with pytest.raises(export.ExportError, match="export_busy"):
            build()
    finally:
        archive.close()
        archive.close()
    again = build()
    again.close()


def test_insufficient_disk_space_refuses_before_creating_archive(studio, monkeypatch):
    from collections import namedtuple
    client, factory, _, _ = studio
    url = client.get(_path()).json()["items"][0]["download_url"]
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(export.shutil, "disk_usage", lambda path: Usage(100, 90, 10))
    monkeypatch.setattr(export.tempfile, "TemporaryFile", lambda **kwargs: pytest.fail("wrote before space check"))
    response = client.get(url)
    assert response.status_code == 503 and response.json()["error"] == "export_insufficient_space"
    # Отказ освобождает лимит, поэтому следующий запрос проверяет место снова.
    assert client.get(url).json()["error"] == "export_insufficient_space"


def test_download_busy_response_does_not_start_another_copy(studio):
    client, _, _, _ = studio
    url = client.get(_path()).json()["items"][0]["download_url"]
    assert export._export_slot.acquire(blocking=False)
    try:
        response = client.get(url)
        assert response.status_code == 429 and response.json()["error"] == "export_busy"
    finally:
        export._export_slot.release()


def test_labels_download_is_utf8_txt_identical_to_zip_with_safe_rows(studio):
    client, factory, _, _ = studio
    from urllib.parse import quote
    with factory() as db:
        db.get(AudioFile, "take-export").canonical_filename = 'Chapter02_Проба\tновая\r\n".wav'
        db.commit()
    item = client.get(_path()).json()["items"][0]
    assert item["labels_download_url"].endswith('/sources/take-export/labels.txt')
    response = client.get(item["labels_download_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    disposition = response.headers["content-disposition"]
    assert disposition == 'attachment; filename="narrator-labels.txt"; filename*=UTF-8\'\'' + quote(
        'Chapter02_Проба\tновая\r\n"_labels.txt', safe="")
    assert '\r' not in disposition and '\n' not in disposition and '\t' not in disposition
    with zipfile.ZipFile(io.BytesIO(client.get(item["download_url"]).content)) as archive:
        assert response.content == archive.read("labels.txt")
    rows = response.content.decode("utf-8").splitlines()
    assert len(rows) == 1
    start, end, text = rows[0].split("\t")
    assert (start, end) == ("7.000000", "7.300000")
    assert "Проверьте соседние слова." in text


@pytest.mark.parametrize("location", ["local", "nas"])
def test_labels_need_no_source_storage_even_if_audio_is_absent(studio, monkeypatch, location):
    client, factory, directory, _ = studio
    with factory() as db:
        db.get(AudioFile, "take-export").location = location
        db.commit()
        audit = db.get(AsrAudit, "audit-export")
        metadata = json.loads(audit.lines_json)
        metadata["narrator_input_fingerprint"] = asr_audit.narrator_input_fingerprint(db, CHAPTER)
        audit.lines_json = json.dumps(metadata)
        db.commit()
    (directory / "take.wav").unlink()
    def forbidden(*args, **kwargs):
        pytest.fail("labels must not touch audio storage, hashing or archive runtime")
    monkeypatch.setattr(export, "_open_source", forbidden)
    monkeypatch.setattr(export, "build_archive", forbidden)
    monkeypatch.setattr(export.audio_storage, "resolve_path", forbidden)
    monkeypatch.setattr(export.audio_storage, "local_root", forbidden)
    monkeypatch.setattr(export.audio_storage, "nas_root", forbidden)
    monkeypatch.setattr(export.hashlib, "md5", forbidden)
    monkeypatch.setattr(export.tempfile, "TemporaryFile", forbidden)
    from app.v2 import narrator_audit_api
    monkeypatch.setattr(narrator_audit_api, "build_archive", forbidden)
    item = client.get(_path()).json()["items"][0]
    response = client.get(item["labels_download_url"])
    assert response.status_code == 200 and response.content.startswith(b"7.000000\t7.300000\t")
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("uid,expected", [(None, 401), ("other", 403), ("editor", 200), ("missing", 403)])
def test_labels_recheck_current_db_permissions(studio, uid, expected):
    client, _, _, _ = studio
    url = client.get(_path()).json()["items"][0]["labels_download_url"]
    client.cookies.clear()
    if uid:
        _login(client, uid, roles=["admin"])
    assert client.get(url).status_code == expected


def test_labels_reject_stale_audits_and_unbound_audio_ids(studio):
    client, factory, _, _ = studio
    item = client.get(_path()).json()["items"][0]
    url = item["labels_download_url"]
    assert client.get(url.replace("audit-export/", "other-audit/")).status_code == 409
    assert client.get(url.replace("take-export/", "other-take/")).status_code == 404
    with factory() as db:
        db.get(V2Segment, "segment-export").text += " И ещё."
        db.get(V2Attribution, "attribution-export").span_end += len(" И ещё.")
        db.commit()
    assert client.get(url).status_code == 409


@pytest.mark.parametrize("change", ["assignment", "rights", "text", "finding", "filename", "unpublished"])
def test_labels_recheck_after_build_before_returning_text(studio, monkeypatch, change):
    client, factory, _, _ = studio
    if change == "rights":
        _login(client, "editor")
    url = client.get(_path()).json()["items"][0]["labels_download_url"]
    from app.v2 import narrator_audit_api
    original = narrator_audit_api.build_labels
    def build_then_change(item):
        labels = original(item)
        with factory() as db:
            if change == "assignment":
                db.get(Character, "narrator").actor_name = "Sample"
            elif change == "rights":
                db.query(UserRole).filter_by(user_id="editor").delete()
            elif change == "text":
                db.get(V2Segment, "segment-export").text = "Другой текст."
            elif change == "finding":
                db.get(AsrAudit, "audit-export").findings_json = '{"flags": []}'
            elif change == "filename":
                db.get(AudioFile, "take-export").canonical_filename = "new.wav"
            else:
                db.get(ScriptChapter, CHAPTER).status = "draft"
            db.commit()
        return labels
    monkeypatch.setattr(narrator_audit_api, "build_labels", build_then_change)
    response = client.get(url)
    assert response.status_code in {403, 404, 409}
    assert response.headers["content-type"].startswith("application/json")


def test_labels_with_no_safe_anchors_are_valid_empty_txt(studio):
    client, factory, _, _ = studio
    _change_flag(factory, lambda flag: flag["word_anchors"][0].update(mapped=False))
    item = client.get(_path()).json()["items"][0]
    response = client.get(item["labels_download_url"])
    assert response.status_code == 200 and response.content == b""
    assert response.headers["content-type"] == "text/plain; charset=utf-8"


def test_typed_narrator_is_not_confused_with_a_character_named_narrator(studio):
    client, factory, _, _ = studio
    from app.services.role_identity import NARRATOR_REF
    with factory() as db:
        book = db.get(ScriptBook, "book-export")
        book.role_identity_mode = "typed_v1"
        book.language = "en"
        db.get(AudioFile, "take-export").role_ref = NARRATOR_REF
        db.get(V2Attribution, "attribution-export").role_ref = NARRATOR_REF
        db.add(Character(id="named-narrator", book_id=book.id, name="Narrator", actor_name="Sample"))
        db.flush()
        audit = db.get(AsrAudit, "audit-export")
        metadata = json.loads(audit.lines_json)
        metadata["narrator_input_fingerprint"] = asr_audit.narrator_input_fingerprint(db, CHAPTER)
        audit.lines_json = json.dumps(metadata)
        db.commit()
    response = client.get(_path())
    assert response.status_code == 200 and len(response.json()["items"]) == 1
    item = response.json()["items"][0]
    assert client.get(item["labels_download_url"]).status_code == 200
    assert client.get(item["download_url"]).status_code == 200
    _login(client, "other")
    assert client.get(_path()).status_code == 403
    assert client.get(item["labels_download_url"]).status_code == 403


def test_metadata_only_snapshot_preserves_physical_layout_fingerprint(studio, monkeypatch):
    _, factory, _, _ = studio
    from app.services import chapter_delivery
    with factory() as db:
        metadata_snapshot = asr_audit._narrator_snapshot(db, CHAPTER)
        original = chapter_delivery._script_layout
        def physical_layout(*args, **kwargs):
            kwargs["metadata_only"] = False
            return original(*args, **kwargs)
        monkeypatch.setattr(chapter_delivery, "_script_layout", physical_layout)
        assert asr_audit._narrator_snapshot(db, CHAPTER) == metadata_snapshot


def test_online_nas_zip_uses_public_storage_key_without_private_layout(studio, monkeypatch):
    client, factory, directory, content = studio
    nas = directory / "nas"
    (nas / "nested").mkdir(parents=True)
    (nas / "nested" / "take.wav").write_bytes(content)
    with factory() as db:
        audio = db.get(AudioFile, "take-export")
        audio.location, audio.stored_key = "nas", "nested/take.wav"
        db.flush()
        audit = db.get(AsrAudit, "audit-export")
        metadata = json.loads(audit.lines_json)
        metadata["narrator_input_fingerprint"] = asr_audit.narrator_input_fingerprint(db, CHAPTER)
        audit.lines_json = json.dumps(metadata)
        db.commit()
    monkeypatch.setattr("app.services.nas_health.nas_online", lambda: True)
    monkeypatch.setattr(export.audio_storage, "nas_root", lambda: str(nas))
    monkeypatch.setattr(export.audio_storage, "local_root", lambda: pytest.fail("NAS export read local storage"))
    item = client.get(_path()).json()["items"][0]
    response = client.get(item["download_url"])
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read("source.wav") == content
        assert archive.read("labels.txt") == client.get(item["labels_download_url"]).content
