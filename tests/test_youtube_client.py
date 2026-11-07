"""Сеть подменена: проверяем границы доверия, а не доступность YouTube из CI."""
import json
import subprocess
import time

import pytest

from app.services import youtube_client as youtube


IDENTITY = "dQw4w9WgXcQ"
OTHER = "abcdefghijk"


def encoded(*rows):
    return b"\n".join(json.dumps(row, ensure_ascii=False).encode() for row in rows)


def video(**changes):
    return {"id": IDENTITY, "title": "Тихий дождь", "channel": "Studio",
            "duration": 123.5, "thumbnail": f"https://i.ytimg.com/vi/{IDENTITY}/hqdefault.jpg",
            "license": "Creative Commons Attribution license (reuse allowed)", **changes}


@pytest.mark.parametrize("value", [IDENTITY, f"https://www.youtube.com/watch?v={IDENTITY}",
    f"https://youtube.com/watch?v={IDENTITY}&t=13&si=share", f"https://youtu.be/{IDENTITY}?si=share",
    f"https://m.youtube.com/shorts/{IDENTITY}", f"https://youtube.com/embed/{IDENTITY}",
    f"https://youtube.com/live/{IDENTITY}", f"http://music.youtube.com/watch?v={IDENTITY}"])
def test_only_video_id_is_forwarded_from_supported_urls(value):
    calls = []
    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return encoded(video())
    result = youtube.YouTubeClient(runner=runner).detail(value)
    assert result["url"] == f"https://www.youtube.com/watch?v={IDENTITY}"
    assert calls[0][0][-2:] == ["--", result["url"]]
    assert calls[0][1] == {"timeout": youtube.TIMEOUT, "max_output": youtube.MAX_OUTPUT}


@pytest.mark.parametrize("value", [None, "", "too_short", "--exec=evil", "ytsearch1:foo",
    "file:///etc/passwd", "http://127.0.0.1/secret", "http://169.254.169.254/latest/meta-data/",
    f"https://youtube.com.evil.test/watch?v={IDENTITY}", f"https://evil.test/youtube.com/watch?v={IDENTITY}",
    f"https://youtube.com@127.0.0.1/watch?v={IDENTITY}", f"https://user:pass@youtube.com/watch?v={IDENTITY}",
    f"https://youtube.com:443/watch?v={IDENTITY}", f"https://youtube.com/watch?v={IDENTITY}&list=PL123",
    f"https://youtu.be/{IDENTITY}?list=PL123", "https://youtube.com/playlist?list=PL123",
    f"https://youtube.com/watch?v={IDENTITY}&v={OTHER}", f"https://youtube.com/watch?v={IDENTITY}\n",
    f"https://youtu.be/{IDENTITY}/extra", "https://youtube.com/@channel", "https://[::1]/watch?v=abcdefghijk",
    "https://[broken/watch", "https://youtu.be/" + "x" * 2100])
def test_invalid_urls_and_playlists_never_reach_the_runner(value):
    calls = []
    client = youtube.YouTubeClient(runner=lambda *a, **kw: calls.append(a))
    with pytest.raises(ValueError, match="bad_youtube_id"):
        client.detail(value)
    assert calls == []


def test_search_is_bounded_text_and_never_interprets_shell_or_url_options():
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return encoded(video(), video(id=OTHER, license=None, channel=None, uploader="Backup"))
    result = youtube.YouTubeClient(runner=runner).search("  rain $(touch /tmp/no) --exec bad  ", limit=2)
    assert calls[0][-2:] == ["--", "ytsearch2:rain $(touch /tmp/no) --exec bad"]
    assert "--flat-playlist" in calls[0]
    assert "--skip-download" in calls[0]
    assert "--ignore-config" in calls[0] and "--no-plugin-dirs" in calls[0]
    assert "--no-cookies" in calls[0] and "--no-cookies-from-browser" in calls[0]
    assert result["has_more"] is False
    assert result["results"][0]["duration_seconds"] == 123.5
    assert result["results"][1]["channel"] == "Backup"
    assert result["results"][1]["license"] == ""
    # Даже явная CC-метка не доказывает, что загрузивший владел правами на запись.
    assert all(row["rights_status"] == "unknown" for row in result["results"])
    assert all("Права не проверены" in row["rights_warning"] for row in result["results"])


@pytest.mark.parametrize("query,limit", [(None, 1), ("", 1), (" " * 3, 1), ("x" * 201, 1),
    ("rain\nforest", 1), ("rain\x00", 1), ("rain", 0), ("rain", 11), ("rain", True), ("rain", "2")])
def test_bad_search_never_starts_a_process(query, limit):
    client = youtube.YouTubeClient(runner=lambda *a, **kw: pytest.fail("runner called"))
    with pytest.raises(ValueError, match="bad_youtube_search"):
        client.search(query, limit)


@pytest.mark.parametrize("payload", [b"not json", b"\xff", b"[]", b"{}", b'{"id":"file:///secret"}',
    b"x" * (youtube.MAX_OUTPUT + 1), "{}", encoded(video(), video())])
def test_bad_detail_output_does_not_become_success(payload):
    client = youtube.YouTubeClient(runner=lambda *a, **kw: payload)
    with pytest.raises(youtube.YouTubeError) as exc:
        client.detail(IDENTITY)
    assert (exc.value.code, exc.value.status) == ("youtube_bad_response", 502)


def test_detail_cannot_substitute_another_video():
    client = youtube.YouTubeClient(runner=lambda *a, **kw: encoded(video(id=OTHER)))
    with pytest.raises(youtube.YouTubeError, match="youtube_bad_response"):
        client.detail(IDENTITY)


def test_search_empty_success_duplicates_and_result_count_limit():
    assert youtube.YouTubeClient(runner=lambda *a, **kw: b"").search("rain")["results"] == []
    client = youtube.YouTubeClient(runner=lambda *a, **kw: encoded(video(), video()))
    assert len(client.search("rain")["results"]) == 1
    with pytest.raises(youtube.YouTubeError, match="youtube_bad_response"):
        client.search("rain", limit=1)


@pytest.mark.parametrize("thumbnail", ["javascript:alert(1)", "http://127.0.0.1/secret",
    "https://evil.test/thumbnail.jpg", "https://user:pass@i.ytimg.com/image", "https://i.ytimg.com:443/image"])
def test_thumbnail_does_not_return_arbitrary_external_urls(thumbnail):
    row = youtube.YouTubeClient(runner=lambda *a, **kw: encoded(video(thumbnail=thumbnail))).detail(IDENTITY)
    assert row["thumbnail"] == ""


@pytest.mark.parametrize("duration", [True, -1, "4", float("nan"), float("inf"), None])
def test_invalid_durations_are_unknown(duration):
    row = youtube.YouTubeClient(runner=lambda *a, **kw: encoded(video(duration=duration))).detail(IDENTITY)
    assert row["duration_seconds"] is None


def test_missing_optional_fields_and_oversize_text_are_safe():
    row = youtube.YouTubeClient(runner=lambda *a, **kw: encoded({"id": IDENTITY, "title": "x" * 10000})).detail(IDENTITY)
    assert len(row["title"]) == 300
    assert row["channel"] == row["license"] == row["thumbnail"] == ""
    assert row["duration_seconds"] is None


def test_runner_errors_hide_stderr_and_process_arguments():
    def runner(*a, **kw):
        raise subprocess.CalledProcessError(1, ["secret"], stderr=b"private details")
    with pytest.raises(youtube.YouTubeError) as exc:
        youtube.YouTubeClient(runner=runner).search("rain")
    assert str(exc.value) == "youtube_unavailable"
    assert exc.value.__cause__ is None


def local_executable(monkeypatch, tmp_path, body):
    executable = tmp_path / "yt-dlp-local-test"
    executable.write_text("#!/usr/bin/python3\n" + body)
    executable.chmod(0o700)
    monkeypatch.setattr(youtube.shutil, "which", lambda *a, **kw: str(executable))


def test_real_process_does_not_inherit_credentials_or_stdin(monkeypatch, tmp_path):
    monkeypatch.setenv("YOUTUBE_TOKEN", "must-not-leak")
    local_executable(monkeypatch, tmp_path,
        "import os, sys, json\nprint(json.dumps({'token': os.environ.get('YOUTUBE_TOKEN'), 'stdin': sys.stdin.read()}))\n")
    result = youtube._run(["yt-dlp", "ignored"], timeout=2, max_output=1000)
    assert json.loads(result) == {"token": None, "stdin": ""}


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_real_process_output_limit_applies_while_reading(monkeypatch, tmp_path, stream):
    local_executable(monkeypatch, tmp_path, f"import sys\nwhile True: sys.{stream}.write('x' * 65536); sys.{stream}.flush()\n")
    start = time.monotonic()
    with pytest.raises(youtube.YouTubeError, match="youtube_bad_response"):
        youtube._run(["yt-dlp"], timeout=2, max_output=1000)
    assert time.monotonic() - start < 2


def test_real_process_is_killed_on_timeout(monkeypatch, tmp_path):
    local_executable(monkeypatch, tmp_path, "import time\ntime.sleep(30)\n")
    start = time.monotonic()
    with pytest.raises(youtube.YouTubeError) as exc:
        youtube._run(["yt-dlp"], timeout=0.05, max_output=1000)
    assert (exc.value.code, exc.value.status) == ("youtube_timeout", 504)
    assert time.monotonic() - start < 2


def test_missing_binary_is_an_explicit_service_error(monkeypatch):
    monkeypatch.setattr(youtube.shutil, "which", lambda *a, **kw: None)
    with pytest.raises(youtube.YouTubeError, match="youtube_not_installed"):
        youtube._run(["yt-dlp"], timeout=1, max_output=1000)
