import hashlib
import json
from pathlib import Path
import subprocess
import sys
import shutil

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import AudioFile, Base, ScriptBook, ScriptChapter
from app.services import audio_storage, youtube_download as yd


VIDEO = "abcdefghijk"
TASK = "11111111-2222-4333-8444-555555555555"
METADATA = {"id": VIDEO, "title": "Музыка", "duration": 120, "uploader": "Канал",
    "formats": [{"ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"}]}
PROBE = {"duration_seconds": 119.5, "sample_rate": 48000, "channels": 2, "codec": "aac", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"}
LIMITS = yd.DownloadLimits(max_duration_seconds=3600, max_bytes=1024, reserve_bytes=100)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(audio_storage.settings, "audio_storage_path", str(tmp_path / "audio"))
    (tmp_path / "audio").mkdir()
    monkeypatch.setattr(audio_storage, "free_bytes", lambda path: 10000)
    monkeypatch.setattr(audio_storage, "nas_root", lambda: pytest.fail("загрузка коснулась NAS"))
    with Session(engine) as db:
        book = ScriptBook(id="book", title="Тестовая книга", source_filename="a.txt", source_format="txt")
        chapter = ScriptChapter(id="chapter", book_id="book", chapter_index=7, chapter_title="Эпилог")
        db.add_all([book, chapter])
        db.commit()
        yield db, tmp_path / "audio"


def runner(metadata=None, content=b"audio"):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "--dump-single-json" in argv:
            return json.dumps(METADATA if metadata is None else metadata)
        (Path(kwargs["cwd"]) / "audio.m4a").write_bytes(content)
        return ""

    return run, calls


def download(db, **kwargs):
    run, _ = runner()
    options = dict(video_id=VIDEO, chapter_id="chapter", task_id=TASK, rights_confirmed=True, run=run, probe=lambda path: PROBE, limits=LIMITS)
    options.update(kwargs)
    return yd.download_youtube_audio(db, **options)


def test_import_verifies_then_saves_local_audio_and_keeps_source(environment):
    db, root = environment
    run, calls = runner()
    result = download(db, run=run)
    item = result.audio_file
    assert item.kind == "youtube"
    assert result.provenance["book_id"] == "book" and result.provenance["chapter_id"] == "chapter"
    assert item.stored_key.startswith("YouTube/book/")
    assert "/Chapter07/" in item.stored_key
    assert (root / item.stored_key).read_bytes() == b"audio"
    assert item.md5 == hashlib.md5(b"audio").hexdigest()
    assert item.duration_seconds == 119.5 and item.codec == "aac"
    assert item.verify_state == "ok" and item.verified_at is not None
    assert item.location == "local" and item.mirror_state == "" and item.mirrored_at is None
    assert result.provenance["source_url"] == f"https://www.youtube.com/watch?v={VIDEO}"
    assert result.provenance["rights_status"] == "owner_confirmed"
    assert result.provenance["declared_license"] == ""
    assert not list(root.glob(".youtube-*"))
    assert len(calls) == 2
    for command, arguments in calls:
        assert command[-2:] == ["--", f"https://www.youtube.com/watch?v={VIDEO}"]
        assert "--no-playlist" in command and "--ignore-config" in command
        assert arguments["reserve_bytes"] == 100
    assert calls[1][1]["timeout"] == LIMITS.timeout_seconds
    assert "--max-filesize" in calls[1][0]
    # Транзакция остаётся вызывающему: никакого скрытого коммита.
    db.rollback()
    assert db.query(AudioFile).count() == 0
    result.cleanup()
    assert not (root / item.stored_key).exists()


@pytest.mark.parametrize("video_id", ["", "a" * 12, "../abcdefgh", "abcdefghij;", "https://youtu.be/abcdefghijk", "abcdefghij\n", "абвгдежзийк", None])
def test_only_strict_video_ids_are_accepted(environment, video_id):
    db, root = environment
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_invalid_video_id"):
        download(db, video_id=video_id, run=lambda *a, **kw: pytest.fail("загрузчик вызван"))
    assert not list(root.iterdir())


@pytest.mark.parametrize("confirmed", [False, None, 1, "true"])
def test_requires_explicit_rights_confirmation(environment, confirmed):
    db, root = environment
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_rights_confirmation_required"):
        download(db, rights_confirmed=confirmed)
    assert not list(root.iterdir())


def test_unknown_chapter_stops_before_downloader(environment):
    db, root = environment
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_chapter_not_found"):
        download(db, chapter_id="absent", run=lambda *a, **kw: pytest.fail("загрузчик вызван"))
    assert not list(root.iterdir())


@pytest.mark.parametrize("metadata, error", [
    ({**METADATA, "duration": 3601}, "youtube_too_long"),
    ({**METADATA, "duration": None}, "youtube_invalid_duration"),
    ({**METADATA, "duration": True}, "youtube_invalid_duration"),
    ({**METADATA, "duration": float("nan")}, "youtube_invalid_duration"),
    ({**METADATA, "is_live": True}, "youtube_live_not_supported"),
    ({**METADATA, "live_status": "is_upcoming"}, "youtube_live_not_supported"),
    ({**METADATA, "id": "differentid"}, "youtube_invalid_metadata"),
    ({**METADATA, "_type": "playlist"}, "youtube_invalid_metadata"),
    ([], "youtube_invalid_metadata"),
])
def test_metadata_checks_before_file_download(environment, metadata, error):
    db, root = environment
    run, calls = runner(metadata)
    with pytest.raises(yd.YoutubeDownloadError, match=error):
        download(db, run=run)
    assert len(calls) == 1
    assert db.query(AudioFile).count() == 0
    assert not list(root.rglob("*"))


@pytest.mark.parametrize("info, error", [
    ({}, "youtube_invalid_duration"),
    ({**PROBE, "duration_seconds": 3601}, "youtube_too_long"),
    ({**PROBE, "channels": 0}, "youtube_invalid_audio"),
    ({**PROBE, "codec": ""}, "youtube_invalid_audio"),
])
def test_real_file_probe_is_required_before_database_row(environment, info, error):
    db, root = environment
    with pytest.raises(yd.YoutubeDownloadError, match=error):
        download(db, probe=lambda path: info)
    assert db.query(AudioFile).count() == 0
    assert not list(root.rglob("*"))


@pytest.mark.parametrize("content, error", [(b"", "youtube_empty_download"), (b"a" * 1025, "youtube_too_large")])
def test_download_size_is_checked_again(environment, content, error):
    db, root = environment
    run, _ = runner(content=content)
    with pytest.raises(yd.YoutubeDownloadError, match=error):
        download(db, run=run)
    assert db.query(AudioFile).count() == 0
    assert not list(root.rglob("*"))


@pytest.mark.parametrize("formats", [None, [],
    [{"ext": "webm", "vcodec": "none", "acodec": "opus"}],
    [{"ext": "m4a", "vcodec": "none", "acodec": "opus"}],
    [{"ext": "m4a", "vcodec": "h264", "acodec": "mp4a.40.2"}],
])
def test_missing_audition_format_is_rejected_before_download(environment, formats):
    db, root = environment
    run, calls = runner(metadata={**METADATA, "formats": formats})
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_audition_format_unavailable"):
        download(db, run=run)
    assert len(calls) == 1
    assert db.query(AudioFile).count() == 0 and not list(root.rglob("*"))


@pytest.mark.parametrize("acodec", ["mp4a.40.2", "aac"])
def test_format_selection_excludes_non_aac_even_when_it_has_higher_bitrate(environment, tmp_path, acodec):
    db, root = environment
    metadata = {**METADATA, "formats": [
        {"format_id": "aac", "ext": "m4a", "vcodec": "none", "acodec": acodec, "abr": 128, "url": "https://example.invalid/aac"},
        {"format_id": "opus", "ext": "m4a", "vcodec": "none", "acodec": "opus", "abr": 256, "url": "https://example.invalid/opus"},
    ]}
    run, calls = runner(metadata)
    download(db, run=run)
    expression = calls[1][0][calls[1][0].index("--format") + 1]
    assert all("[acodec" in choice for choice in expression.split("/"))
    # --load-info-json и --skip-download проверяют parser без extractor и сети.
    executable = shutil.which("yt-dlp", path="/usr/local/bin:/usr/bin:/bin")
    if executable is None:
        pytest.skip("локальный yt-dlp не установлен")
    info = tmp_path / "info.json"
    info.write_text(json.dumps(metadata))
    selected = subprocess.run([executable, "--ignore-config", "--no-plugin-dirs", "--no-remote-components",
        "--no-check-formats", "--skip-download", "--load-info-json", str(info),
        "--format", expression, "--print", "format_id"],
        check=True, timeout=10, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"})
    assert selected.stdout.strip() == "aac"


@pytest.mark.parametrize("info", [
    {**PROBE, "codec": "opus"},
    {**PROBE, "format_name": "matroska,webm"},
    {**PROBE, "format_name": "aac"},
    {**PROBE, "format_name": ""},
])
def test_actual_container_and_codec_must_match_m4a_aac(environment, info):
    db, root = environment
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_audition_format_invalid"):
        download(db, probe=lambda path: info)
    assert db.query(AudioFile).count() == 0 and not list(root.rglob("*"))


@pytest.mark.parametrize("extension, codec, accepted", [("m4a", "aac", True), ("webm", "libopus", False)])
def test_real_probe_checks_container_even_if_webm_is_renamed_to_m4a(environment, tmp_path, extension, codec, accepted):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("локальные ffmpeg/ffprobe не установлены")
    db, root = environment
    fixture = tmp_path / f"source.{extension}"
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "anullsrc=r=48000:cl=stereo", "-t", "0.2", "-c:a", codec, str(fixture)],
        check=True, timeout=10, stdin=subprocess.DEVNULL, capture_output=True)
    run, _ = runner(content=fixture.read_bytes())
    limits = yd.DownloadLimits(max_bytes=8192, reserve_bytes=100)
    if accepted:
        result = download(db, run=run, probe=None, limits=limits)
        assert result.audio_file.codec == "aac" and result.audio_file.mime_type == "audio/mp4"
        assert result.audio_file.stored_key.endswith(".m4a")
        assert result.audio_file.duration_seconds > 0
    else:
        with pytest.raises(yd.YoutubeDownloadError, match="youtube_audition_format_invalid"):
            download(db, run=run, probe=None, limits=limits)
        assert db.query(AudioFile).count() == 0 and not list(root.rglob("*"))


def test_disk_reserve_prevents_any_download(environment, monkeypatch):
    db, root = environment
    monkeypatch.setattr(audio_storage, "free_bytes", lambda path: 1100)
    with pytest.raises(yd.audio_mirror.LocalDiskFull):
        download(db, run=lambda *a, **kw: pytest.fail("загрузчик вызван"))


def test_disk_reserve_is_rechecked_after_download(environment, monkeypatch):
    db, root = environment
    reads = iter([10000, 99])
    monkeypatch.setattr(audio_storage, "free_bytes", lambda path: next(reads))
    with pytest.raises(yd.audio_mirror.LocalDiskFull):
        download(db)
    assert db.query(AudioFile).count() == 0
    assert not list(root.rglob("*"))


def test_interrupted_download_cleans_partial_file(environment):
    db, root = environment
    def run(argv, **kwargs):
        if "--dump-single-json" in argv:
            return json.dumps(METADATA)
        (Path(kwargs["cwd"]) / "audio.m4a.part").write_bytes(b"partial")
        raise yd.YoutubeDownloadError("youtube_timeout")
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_timeout"):
        download(db, run=run)
    assert not list(root.rglob("*"))
    assert db.query(AudioFile).count() == 0


def test_other_book_folder_cannot_override_isolated_youtube_storage(environment):
    db, root = environment
    db.add(AudioFile(book_code="OLD", chapter="Глава 1", role="А", original_filename="old.wav",
        stored_key="OLD/Chapter01/old.wav", mime_type="audio/wav", size_bytes=1))
    db.commit()
    result = download(db)
    assert result.audio_file.stored_key.startswith("YouTube/book/")
    assert "/OLD/" not in result.audio_file.stored_key


def test_flush_failure_removes_the_final_file(environment, monkeypatch):
    db, root = environment

    def fail(*args, **kwargs):
        raise RuntimeError("database unavailable")

    # autoflush при чтении ещё работает, ломается только явный финальный flush.
    original_flush = db.flush
    def final_flush(*args, **kwargs):
        if any(isinstance(obj, AudioFile) for obj in db.new):
            fail()
        return original_flush(*args, **kwargs)
    monkeypatch.setattr(db, "flush", final_flush)
    with pytest.raises(RuntimeError, match="database unavailable"):
        download(db)
    assert not [p for p in root.rglob("*") if p.is_file()]


def test_existing_destination_is_reused_only_for_identical_bytes(environment):
    db, root = environment
    first = download(db)
    first_path = root / first.audio_file.stored_key
    db.commit()
    second = download(db)
    assert second.audio_file.id == first.audio_file.id
    assert db.query(AudioFile).count() == 1
    second.cleanup()
    assert first_path.read_bytes() == b"audio"
    run, _ = runner(content=b"other")
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_storage_conflict"):
        download(db, run=run)
    assert first_path.read_bytes() == b"audio"


def test_retry_adopts_orphan_but_cleanup_does_not_remove_it(environment):
    db, root = environment
    first = download(db)
    path = root / first.audio_file.stored_key
    db.rollback()
    second = download(db)
    assert second.audio_file.stored_key == str(path.relative_to(root))
    db.rollback()
    second.cleanup()
    assert path.read_bytes() == b"audio"


def test_missing_storage_root_is_never_created(environment):
    db, root = environment
    root.rmdir()
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_storage_unavailable"):
        download(db)
    assert not root.exists()


@pytest.mark.parametrize("task_id", [None, "", "../unsafe", "not-a-uuid"])
def test_task_id_is_a_uuid(environment, task_id):
    db, root = environment
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_invalid_task_id"):
        download(db, task_id=task_id)


def test_symlink_download_is_refused(environment):
    db, root = environment
    external = root.parent / "external.m4a"
    external.write_bytes(b"private")
    def run(argv, **kwargs):
        if "--dump-single-json" in argv:
            return json.dumps(METADATA)
        (Path(kwargs["cwd"]) / "audio.m4a").symlink_to(external)
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_invalid_download"):
        download(db, run=run)
    assert external.read_bytes() == b"private"


@pytest.fixture
def local_runner(monkeypatch):
    # Проверяем настоящий lifecycle дочернего процесса, но никогда не запускаем yt-dlp.
    monkeypatch.setattr(yd.shutil, "which", lambda name, *, path: sys.executable)


def test_subprocess_failure_sanitizes_stderr(tmp_path, local_runner):
    with pytest.raises(yd.YoutubeDownloadError, match="^youtube_download_failed$"):
        yd._run([sys.executable, "-c", "import sys; sys.stderr.write('secret'); sys.exit(4)"],
            cwd=str(tmp_path), timeout=5, max_bytes=1024, reserve_bytes=0)


def test_subprocess_timeout_kills_child(tmp_path, local_runner):
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_timeout"):
        yd._run([sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=str(tmp_path), timeout=0.1, max_bytes=1024, reserve_bytes=0)


def test_unknown_content_length_cannot_evade_size_limit(tmp_path, local_runner):
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_too_large"):
        yd._run([sys.executable, "-c", "from pathlib import Path; Path('audio.part').write_bytes(b'a'*2048)"],
            cwd=str(tmp_path), timeout=5, max_bytes=1024, reserve_bytes=0)


def test_runner_uses_no_shell_and_ignores_stdin(tmp_path, monkeypatch, local_runner):
    real_popen = subprocess.Popen
    observed = {}
    def popen(*args, **kwargs):
        observed.update(kwargs)
        return real_popen(*args, **kwargs)
    monkeypatch.setattr(subprocess, "Popen", popen)
    assert yd._run([sys.executable, "-c", "print('ok')"],
        cwd=str(tmp_path), timeout=5, max_bytes=1024, reserve_bytes=0) == "ok\n"
    assert observed["shell"] is False
    assert observed["stdin"] == subprocess.DEVNULL and observed["start_new_session"] is True
    assert observed["env"] == {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}


def test_downloader_uses_trusted_path_and_never_inherits_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/tmp/private-home")
    monkeypatch.setenv("HTTP_PROXY", "http://secret-proxy")
    monkeypatch.setenv("SECRET_KEY", "private")
    lookups = []
    def which(name, *, path):
        lookups.append((name, path))
        return sys.executable
    monkeypatch.setattr(yd.shutil, "which", which)
    script = "import os,json; print(json.dumps({k:os.getenv(k) for k in ['HOME','HTTP_PROXY','SECRET_KEY']}))"
    output = yd._run(["untrusted/yt-dlp", "-c", script], cwd=str(tmp_path), timeout=5, max_bytes=1024, reserve_bytes=0)
    assert json.loads(output) == {"HOME": None, "HTTP_PROXY": None, "SECRET_KEY": None}
    assert lookups == [("yt-dlp", "/usr/local/bin:/usr/bin:/bin")]


def test_downloader_output_is_bounded_before_accumulating_in_memory(tmp_path, local_runner):
    with pytest.raises(yd.YoutubeDownloadError, match="youtube_output_too_large"):
        yd._run(["yt-dlp", "-c", "import sys; sys.stdout.write('x'*3000000)"],
            cwd=str(tmp_path), timeout=5, max_bytes=1024, reserve_bytes=0)
