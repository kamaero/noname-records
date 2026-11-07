"""Метаданные YouTube помогают найти источник, но не подтверждают права на звук."""
from __future__ import annotations

import json
import math
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlsplit


MAX_OUTPUT = 512 * 1024
TIMEOUT = 25
_WINDOWS = sys.platform == "win32"
_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_FIELDS = "%(.{id,title,channel,uploader,duration,thumbnail,license})j"
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
RIGHTS_WARNING = "Права не проверены. Даже отметка Creative Commons не подтверждает права на все элементы записи."


class YouTubeError(Exception):
    def __init__(self, code="youtube_unavailable", status=503):
        self.code, self.status = code, status
        super().__init__(code)


def video_id(value):
    """В yt-dlp уходит новый адрес по ID: параметры и чужие хосты туда не попадают."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("bad_youtube_id")
    value = value.strip()
    if _ID.fullmatch(value):
        return value
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"https", "http"} or parsed.username is not None
                or parsed.password is not None or parsed.port is not None):
            raise ValueError
        host = (parsed.hostname or "").lower()
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=16)
        if any(key.lower() == "list" for key in query):
            raise ValueError
        if host in {"youtu.be", "www.youtu.be"}:
            identity = parsed.path.removeprefix("/")
        elif host in _HOSTS and parsed.path == "/watch":
            values = query.get("v", [])
            if len(values) != 1:
                raise ValueError
            identity = values[0]
        elif host in _HOSTS and re.fullmatch(r"/(?:shorts|embed|live)/[A-Za-z0-9_-]{11}", parsed.path):
            identity = parsed.path.rsplit("/", 1)[1]
        else:
            raise ValueError
        if not _ID.fullmatch(identity):
            raise ValueError
        return identity
    except ValueError:
        raise ValueError("bad_youtube_id") from None


def _run(argv, *, timeout, max_output):
    """Чтение ограничено до накопления в памяти; communicate() позволял бы исчерпать RAM."""
    if _WINDOWS:
        from app.services.youtube_windows import run, WindowsProcessError
        try:
            with tempfile.TemporaryDirectory(prefix="youtube-metadata-") as directory:
                return run(["yt-dlp", *argv[1:]], cwd=directory, timeout=timeout, max_output=max_output)
        except WindowsProcessError as exc:
            code, status = {"not_installed": ("youtube_not_installed", 503),
                            "timeout": ("youtube_timeout", 504),
                            "output_limit": ("youtube_bad_response", 502)}.get(str(exc), ("youtube_unavailable", 503))
            raise YouTubeError(code, status) from None
        except OSError:
            raise YouTubeError() from None
    executable = shutil.which("yt-dlp", path="/usr/local/bin:/usr/bin:/bin")
    if not executable:
        raise YouTubeError("youtube_not_installed")
    try:
        process = subprocess.Popen(
            [executable, *argv[1:]], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, start_new_session=True, cwd="/tmp",
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
    except OSError:
        raise YouTubeError() from None
    deadline = time.monotonic() + timeout
    output, size = [], 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise YouTubeError("youtube_timeout", 504)
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(chunk)
                    if size > max_output:
                        raise YouTubeError("youtube_bad_response", 502)
                    if key.fileobj is process.stdout:
                        output.append(chunk)
            try:
                returncode = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise YouTubeError("youtube_timeout", 504) from None
            if returncode:
                # stderr может содержать URL и детали сервера; наружу идёт только код.
                raise YouTubeError()
            return b"".join(output)
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _text(raw, key, limit=300):
    value = raw.get(key)
    return "".join(c for c in value if ord(c) >= 32 and ord(c) != 127)[:limit] if isinstance(value, str) else ""


def _normalize(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not _ID.fullmatch(raw["id"]):
        raise YouTubeError("youtube_bad_response", 502)
    identity = raw["id"]
    duration = raw.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
        duration = None
    thumbnail = _text(raw, "thumbnail", 2048)
    try:
        parsed = urlsplit(thumbnail)
        if (parsed.scheme != "https" or parsed.hostname not in {"i.ytimg.com", "img.youtube.com"}
                or parsed.username is not None or parsed.password is not None or parsed.port is not None):
            thumbnail = ""
    except ValueError:
        thumbnail = ""
    return {"id": identity, "url": f"https://www.youtube.com/watch?v={identity}",
            "title": _text(raw, "title"), "channel": _text(raw, "channel") or _text(raw, "uploader"),
            "duration_seconds": duration, "thumbnail": thumbnail, "license": _text(raw, "license", 1000),
            "rights_status": "unknown", "rights_warning": RIGHTS_WARNING}


class YouTubeClient:
    def __init__(self, *, runner=None):
        self.runner = runner or _run
        self._slots = threading.BoundedSemaphore(2)

    def _request(self, target, *, search=False):
        argv = ["yt-dlp", "--ignore-config", "--no-plugin-dirs", "--no-remote-components",
                "--no-cache-dir", "--no-cookies", "--no-cookies-from-browser", "--proxy", "",
                "--no-playlist", "--skip-download", "--no-warnings", "--no-progress",
                "--socket-timeout", "8", "--retries", "0", "--extractor-retries", "0",
                "--use-extractors", "youtube,youtube:search", "--print", _FIELDS]
        if search:
            argv.append("--flat-playlist")
        argv.extend(["--", target])
        if not self._slots.acquire(timeout=1):
            raise YouTubeError("youtube_busy")
        try:
            output = self.runner(argv, timeout=TIMEOUT, max_output=MAX_OUTPUT)
        except (OSError, subprocess.SubprocessError):
            raise YouTubeError() from None
        finally:
            self._slots.release()
        if not isinstance(output, bytes) or len(output) > MAX_OUTPUT:
            raise YouTubeError("youtube_bad_response", 502)
        try:
            return [_normalize(json.loads(line)) for line in output.decode("utf-8").splitlines() if line.strip()]
        except (UnicodeError, ValueError, RecursionError, OverflowError):
            raise YouTubeError("youtube_bad_response", 502) from None

    def search(self, query, limit=8):
        if (not isinstance(query, str) or not 1 <= len(query) <= 200 or not query.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in query)
                or type(limit) is not int or not 1 <= limit <= 10):
            raise ValueError("bad_youtube_search")
        rows = self._request(f"ytsearch{limit}:{query.strip()}", search=True)
        if len(rows) > limit:
            raise YouTubeError("youtube_bad_response", 502)
        return {"results": list({row["id"]: row for row in rows}.values()), "has_more": False}

    def detail(self, identity):
        identity = video_id(identity)
        rows = self._request(f"https://www.youtube.com/watch?v={identity}")
        if len(rows) != 1 or rows[0]["id"] != identity:
            raise YouTubeError("youtube_bad_response", 502)
        return rows[0]


client = YouTubeClient()
