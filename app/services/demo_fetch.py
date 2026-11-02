"""Скачать демо по ссылке из письма диктора. Страницы-портфолио не качаются вовсе."""
from __future__ import annotations

import os
import re
import subprocess
import urllib.parse

import requests

from app.services.demo_audio import is_media_file

YANDEX_META = "https://cloud-api.yandex.net/v1/disk/public/resources"
YANDEX_API = YANDEX_META + "/download"
MAX_FOLDER_FILES = 20
MAILRU_DISPATCHER = "https://cloud.mail.ru/api/v2/dispatcher"
FALLBACK_NAME = "демо.bin"


class FetchError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def classify(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host, path = parsed.netloc.lower().removeprefix("www.").removeprefix("m."), parsed.path
    if host in {"disk.yandex.ru", "yadi.sk", "disk.yandex.com"}:
        return "yandex"
    if host in {"youtu.be", "youtube.com"}:
        return "ytdlp"
    if host == "vk.com":
        return "ytdlp" if re.match(r"^/(wall|video|clip)-?\d", path) else "portfolio"
    if host == "t.me":
        if path.startswith("/c/"):
            return "private"
        return "ytdlp" if re.match(r"^/[^/]+/\d+", path) else "portfolio"
    if host == "drive.google.com":
        return "gdrive"
    if host == "cloud.mail.ru" and path.startswith("/public/"):
        return "mailru"
    if host == "mega.nz":
        return "mega"
    return "portfolio"


def _save(response, workdir: str, fallback: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", disposition)
    name = urllib.parse.unquote(match.group(1)) if match else fallback
    try:
        name = name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    path = os.path.join(workdir, os.path.basename(name) or fallback)
    with open(path, "wb") as handle:
        for chunk in response.iter_content(1 << 20):
            handle.write(chunk)
    return path


def _yandex_file(url: str, workdir: str, path: str = "") -> str:
    params = {"public_key": url, **({"path": path} if path else {})}
    meta = requests.get(YANDEX_API, params=params, timeout=30)
    meta.raise_for_status()
    href = (meta.json() or {}).get("href")
    if not href:
        raise FetchError("yandex_no_href")
    return _save(requests.get(href, timeout=300, stream=True), workdir, FALLBACK_NAME)


def _yandex(url: str, workdir: str) -> list[str]:
    """Файл скачивается напрямую, папка обходится по отдельным медиафайлам."""
    meta = requests.get(YANDEX_META, params={"public_key": url, "limit": 200}, timeout=30)
    meta.raise_for_status()
    info = meta.json() or {}
    if info.get("type") != "dir":
        return [_yandex_file(url, workdir)]
    files, queue = [], [info]
    while queue and len(files) < MAX_FOLDER_FILES:
        items = (queue.pop(0).get("_embedded") or {}).get("items") or []
        for item in items:
            if item.get("type") == "dir":
                sub = requests.get(YANDEX_META, params={"public_key": url, "path": item["path"], "limit": 200},
                                   timeout=30)
                sub.raise_for_status()
                queue.append(sub.json() or {})
            elif is_media_file(str(item.get("name") or "")) and len(files) < MAX_FOLDER_FILES:
                files.append(_yandex_file(url, workdir, item["path"]))
    if not files:
        raise FetchError("yandex_empty_folder")
    return files


def _ytdlp(url: str, workdir: str) -> list[str]:
    result = subprocess.run(["yt-dlp", "-q", "--no-playlist", "-f", "bestaudio/best", "-o",
                             os.path.join(workdir, "%(id)s.%(ext)s"), url],
                            capture_output=True, timeout=900)
    files = [os.path.join(workdir, f) for f in os.listdir(workdir)]
    if result.returncode != 0 or not files:
        raise FetchError("ytdlp_failed")
    return files


def _gdrive(url: str, workdir: str) -> list[str]:
    match = re.search(r"/file/d/([^/]+)", url)
    if match:
        response = requests.get("https://drive.google.com/uc", params={"export": "download", "id": match.group(1)},
                                timeout=300, stream=True)
        if "text/html" in response.headers.get("content-type", ""):
            return _ytdlp(url, workdir)
        return [_save(response, workdir, FALLBACK_NAME)]
    return _ytdlp(url, workdir)


def _mailru(url: str, workdir: str) -> list[str]:
    weblink = urllib.parse.urlparse(url).path.removeprefix("/public/")
    dispatcher = requests.get(MAILRU_DISPATCHER, timeout=30).json()
    base = dispatcher["body"]["weblink_get"][0]["url"]
    return [_save(requests.get(f"{base}/{weblink}", timeout=300, stream=True), workdir, FALLBACK_NAME)]


def _mega(url: str, workdir: str) -> list[str]:
    result = subprocess.run(["megadl", "--path", workdir, url], capture_output=True, timeout=900)
    files = [os.path.join(workdir, f) for f in os.listdir(workdir)]
    if result.returncode != 0 or not files:
        raise FetchError("mega_failed")
    return files


def download(url: str, workdir: str) -> list[str]:
    kind = classify(url)
    handlers = {"yandex": _yandex, "ytdlp": _ytdlp, "gdrive": _gdrive, "mailru": _mailru, "mega": _mega}
    if kind not in handlers:
        raise FetchError(kind)
    os.makedirs(workdir, exist_ok=True)
    try:
        return handlers[kind](url, workdir)
    except FetchError:
        raise
    except (requests.RequestException, subprocess.TimeoutExpired, KeyError, ValueError, OSError) as exc:
        raise FetchError(f"{kind}_failed:{type(exc).__name__}") from exc
