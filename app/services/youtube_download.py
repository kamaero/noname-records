"""Импорт выбранного человеком аудио без выдачи его за запись актёра или лицензию."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import stat
import sys
import tempfile
import time
import uuid

from app.config import settings
from app.models import AudioFile, ScriptBook, ScriptChapter
from app.services import audio_mirror, audio_storage
from app.services.audio_uploads import derive_book_code
from app.time_utils import utcnow_naive

KIND = "youtube"
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z", re.ASCII)
_MAX_OUTPUT = 2 * 1024 * 1024
_WINDOWS = sys.platform == "win32"


class YoutubeDownloadError(ValueError):
    """Стабильный код вместо stderr, где могут оказаться служебные пути и URL."""


@dataclass(frozen=True)
class DownloadLimits:
    max_duration_seconds: int = 4 * 3600
    max_bytes: int = 256 * 1024 * 1024
    timeout_seconds: int = 300
    metadata_timeout_seconds: int = 45
    reserve_bytes: int | None = None


@dataclass
class DownloadResult:
    audio_file: AudioFile
    provenance: dict
    _path: Path
    _owns_file: bool = True

    def cleanup(self) -> None:
        """Вызывающий удаляет файл, если его транзакция не смогла закоммититься."""
        if self._owns_file:
            self._path.unlink(missing_ok=True)


def canonical_url(video_id: str) -> str:
    if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
        raise YoutubeDownloadError("youtube_invalid_video_id")
    return f"https://www.youtube.com/watch?v={video_id}"


def _run(argv: list[str], *, cwd: str, timeout: int, max_bytes: int, reserve_bytes: int) -> str:
    """Ограничение yt-dlp по Content-Length недостаточно для неизвестного размера."""
    if _WINDOWS:
        from app.services.youtube_windows import run, WindowsProcessError
        def check_storage():
            total = sum(path.stat().st_size for path in Path(cwd).iterdir() if path.is_file())
            if total > max_bytes:
                raise YoutubeDownloadError("youtube_too_large")
            audio_mirror.ensure_room(0, free=audio_storage.free_bytes(cwd), reserve_bytes=reserve_bytes)
        try:
            binary = "ffprobe" if argv[0] == "ffprobe" else "yt-dlp"
            return run([binary, *argv[1:]], cwd=cwd, timeout=timeout,
                       max_output=_MAX_OUTPUT, check_storage=check_storage).decode("utf-8", errors="replace")
        except WindowsProcessError as exc:
            code = {"not_installed": "youtube_downloader_unavailable", "timeout": "youtube_timeout",
                    "output_limit": "youtube_output_too_large"}.get(str(exc), "youtube_download_failed")
            raise YoutubeDownloadError(code) from None
        except OSError:
            raise YoutubeDownloadError("youtube_downloader_unavailable") from None
    started = time.monotonic()
    binary = "ffprobe" if argv[0] == "ffprobe" else "yt-dlp"
    executable = shutil.which(binary, path="/usr/local/bin:/usr/bin:/bin")
    if not executable:
        raise YoutubeDownloadError("youtube_downloader_unavailable")
    try:
        process = subprocess.Popen(
            [executable, *argv[1:]], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, start_new_session=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
    except OSError as exc:
        raise YoutubeDownloadError("youtube_downloader_unavailable") from exc
    output, output_size = [], 0
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map() or process.poll() is None:
                total = sum(p.stat().st_size for p in Path(cwd).iterdir() if p.is_file())
                if total > max_bytes:
                    raise YoutubeDownloadError("youtube_too_large")
                audio_mirror.ensure_room(0, free=audio_storage.free_bytes(cwd), reserve_bytes=reserve_bytes)
                if time.monotonic() - started > timeout:
                    raise YoutubeDownloadError("youtube_timeout")
                for key, _ in selector.select(timeout=0.05):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    output_size += len(chunk)
                    if output_size > _MAX_OUTPUT:
                        raise YoutubeDownloadError("youtube_output_too_large")
                    if key.fileobj is process.stdout:
                        output.append(chunk)
                if not selector.get_map() and process.poll() is None:
                    time.sleep(0.05)
            if sum(p.stat().st_size for p in Path(cwd).iterdir() if p.is_file()) > max_bytes:
                raise YoutubeDownloadError("youtube_too_large")
            audio_mirror.ensure_room(0, free=audio_storage.free_bytes(cwd), reserve_bytes=reserve_bytes)
            if process.returncode:
                raise YoutubeDownloadError("youtube_download_failed")
            return b"".join(output).decode("utf-8", errors="replace")
    finally:
        # Отменённая задача не должна оставлять дочерний загрузчик писать на диск.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()
        process.stderr.close()


def _duration(value, maximum: int) -> float:
    if isinstance(value, bool):
        raise YoutubeDownloadError("youtube_invalid_duration")
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise YoutubeDownloadError("youtube_invalid_duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise YoutubeDownloadError("youtube_invalid_duration")
    if duration > maximum:
        raise YoutubeDownloadError("youtube_too_long")
    return duration


def _inside(root: Path, path: Path) -> None:
    if not path.resolve().is_relative_to(root.resolve()):
        raise YoutubeDownloadError("youtube_unsafe_storage_path")
    if _WINDOWS:
        # Junction тоже перенаправляет путь, хотя is_symlink() возвращает False.
        for component in (path, *path.parents):
            try:
                information = component.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(information.st_mode) or getattr(information, "st_file_attributes", 0) & 0x400:
                raise YoutubeDownloadError("youtube_unsafe_storage_path")
            if component == root:
                break


def _publish(source, destination):
    if _WINDOWS:
        from app.services.youtube_windows import WinAPI
        WinAPI().publish(source, destination)
    else:
        os.link(source, destination)
        try:
            descriptor = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise


def _open_previous(path):
    if _WINDOWS:
        from app.services.youtube_windows import WinAPI
        return WinAPI().open_existing(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    return os.fdopen(descriptor, "rb")


def _probe_for_audition(path: str, *, reserve_bytes: int, max_bytes: int) -> dict:
    """Расширение от загрузчика не доказывает контейнер и кодек готового файла."""
    raw = _run([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file,crypto,data",
        "-show_entries", "format=format_name,duration:stream=codec_type,codec_name,sample_rate,channels",
        "-of", "json", str(path),
    ], cwd=str(Path(path).parent), timeout=30, max_bytes=max_bytes, reserve_bytes=reserve_bytes)
    try:
        data = json.loads(raw)
        streams = data["streams"]
        audio = streams[0]
        if len(streams) != 1 or audio["codec_type"] != "audio":
            raise ValueError
        return {
            "duration_seconds": data["format"]["duration"],
            "format_name": data["format"]["format_name"],
            "codec": audio["codec_name"], "sample_rate": audio["sample_rate"], "channels": audio["channels"],
        }
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise YoutubeDownloadError("youtube_audition_format_invalid") from exc


def download_youtube_audio(
    db, *, video_id: str, chapter_id: str, task_id: str, rights_confirmed: bool,
    run=None, probe=None, limits: DownloadLimits | None = None,
) -> DownloadResult:
    """Только flush: вызывающий сохраняет provenance и коммитит общую транзакцию.

    При ошибке commit вызывающий обязан вызвать result.cleanup() после rollback.
    Авторизацию главы и права оператора проверяет API до постановки задачи в очередь.
    """
    url = canonical_url(video_id)
    try:
        stable_id = str(uuid.UUID(task_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise YoutubeDownloadError("youtube_invalid_task_id") from exc
    if rights_confirmed is not True:
        raise YoutubeDownloadError("youtube_rights_confirmation_required")
    limits = limits or DownloadLimits()
    if any(type(value) is not int or value <= 0 for value in (
        limits.max_duration_seconds, limits.max_bytes, limits.timeout_seconds, limits.metadata_timeout_seconds,
    )) or (limits.reserve_bytes is not None and (type(limits.reserve_bytes) is not int or limits.reserve_bytes < 0)):
        raise YoutubeDownloadError("youtube_invalid_limits")
    reserve = limits.reserve_bytes if limits.reserve_bytes is not None else int(settings.local_reserve_gb) * 1024**3
    chapter = db.get(ScriptChapter, chapter_id)
    book = db.get(ScriptBook, chapter.book_id) if chapter is not None else None
    if book is None:
        raise YoutubeDownloadError("youtube_chapter_not_found")
    # В публичной схеме AudioFile нет book_id: связь хранит журнал загрузки,
    # а ID книги в пути исключает совпадения одинаковых названий.
    book_code = derive_book_code(book.title)
    root = Path(audio_storage.local_root())
    if not root.is_dir():
        raise YoutubeDownloadError("youtube_storage_unavailable")
    audio_mirror.ensure_room(limits.max_bytes, free=audio_storage.free_bytes(str(root)), reserve_bytes=reserve)
    runner = run or _run
    probe = probe or (lambda path: _probe_for_audition(path, reserve_bytes=reserve, max_bytes=limits.max_bytes))
    common = [
        "yt-dlp", "--ignore-config", "--no-plugin-dirs", "--no-remote-components",
        "--no-cookies", "--no-cookies-from-browser", "--proxy", "", "--use-extractors", "youtube",
        "--no-playlist", "--no-cache-dir", "--no-warnings",
        "--socket-timeout", "15", "--retries", "1", "--extractor-retries", "1",
    ]
    final_path = None
    try:
        with tempfile.TemporaryDirectory(prefix=".youtube-", dir=root) as temporary:
            raw = runner(common + ["--skip-download", "--dump-single-json", "--", url],
                cwd=temporary, timeout=limits.metadata_timeout_seconds, max_bytes=limits.max_bytes, reserve_bytes=reserve)
            try:
                metadata = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise YoutubeDownloadError("youtube_invalid_metadata") from exc
            if not isinstance(metadata, dict) or metadata.get("id") != video_id or metadata.get("_type", "video") != "video":
                raise YoutubeDownloadError("youtube_invalid_metadata")
            if metadata.get("is_live") or metadata.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
                raise YoutubeDownloadError("youtube_live_not_supported")
            _duration(metadata.get("duration"), limits.max_duration_seconds)
            formats = metadata.get("formats")
            if not isinstance(formats, list) or not any(
                isinstance(item, dict) and item.get("ext") == "m4a" and item.get("vcodec") == "none"
                and (str(item.get("acodec") or "").startswith("mp4a") or item.get("acodec") == "aac")
                for item in formats
            ):
                raise YoutubeDownloadError("youtube_audition_format_unavailable")
            runner(common + [
                # WebM/Opus не выдаём человеку как материал, готовый для Audition.
                "--format", "bestaudio[ext=m4a][vcodec=none][acodec^=mp4a]/bestaudio[ext=m4a][vcodec=none][acodec=aac]",
                "--fixup", "never",
                "--no-progress", "--no-continue", "--no-overwrites", "--no-mtime",
                "--fragment-retries", "1", "--concurrent-fragments", "1",
                "--max-filesize", str(limits.max_bytes),
                "--match-filter", f"!is_live & duration <= {limits.max_duration_seconds}",
                "--output", "audio.%(ext)s", "--", url,
            ], cwd=temporary, timeout=limits.timeout_seconds, max_bytes=limits.max_bytes, reserve_bytes=reserve)
            files = list(Path(temporary).iterdir())
            if len(files) != 1 or files[0].name != "audio.m4a" or files[0].is_symlink() or not files[0].is_file():
                raise YoutubeDownloadError("youtube_invalid_download")
            source = files[0]
            _inside(root, source)
            size = source.stat().st_size
            if not 0 < size <= limits.max_bytes:
                raise YoutubeDownloadError("youtube_too_large" if size else "youtube_empty_download")
            audio_mirror.ensure_room(0, free=audio_storage.free_bytes(str(root)), reserve_bytes=reserve)
            info = probe(str(source))
            if not isinstance(info, dict):
                raise YoutubeDownloadError("youtube_invalid_audio")
            duration = _duration(info.get("duration_seconds"), limits.max_duration_seconds)
            sample_rate, channels = int(info.get("sample_rate") or 0), int(info.get("channels") or 0)
            codec = str(info.get("codec") or "")
            if sample_rate <= 0 or channels <= 0 or not codec:
                raise YoutubeDownloadError("youtube_invalid_audio")
            if codec != "aac" or not {"mov", "mp4", "m4a"}.intersection(str(info.get("format_name") or "").split(",")):
                raise YoutubeDownloadError("youtube_audition_format_invalid")
            digest = hashlib.md5()
            with source.open("r+b") as content:
                for chunk in iter(lambda: content.read(1024 * 1024), b""):
                    digest.update(chunk)
                os.fsync(content.fileno())
            filename = f"youtube_{stable_id}{source.suffix}"
            key = audio_mirror.storage_key(book_code, f"Глава {chapter.chapter_index}", filename, KIND)
            key = str(Path("YouTube") / book.id / key)
            destination = Path(audio_storage.resolve_path(key))
            _inside(root, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _inside(root, destination)
            # Hard link публикует готовый файл атомарно и никогда не затирает старый.
            os.chmod(source, 0o600)
            alternate = destination.with_suffix(".webm")
            if alternate.exists() or alternate.is_symlink():
                raise YoutubeDownloadError("youtube_storage_conflict")
            owns_file = True
            try:
                _publish(source, destination)
                final_path = destination
            except FileExistsError:
                # После аварии файл может пережить транзакцию. Повтор допустим
                # лишь для тех же байтов; чужой или изменившийся материал не затираем.
                owns_file = False
                previous = hashlib.md5()
                with _open_previous(destination) as old:
                    if os.fstat(old.fileno()).st_size != size:
                        raise YoutubeDownloadError("youtube_storage_conflict")
                    for chunk in iter(lambda: old.read(1024 * 1024), b""):
                        previous.update(chunk)
                if previous.hexdigest() != digest.hexdigest():
                    raise YoutubeDownloadError("youtube_storage_conflict")
            prior = db.query(AudioFile).filter(AudioFile.stored_key == key).first()
            if prior is not None and (prior.md5 != digest.hexdigest() or prior.size_bytes != size or prior.kind != KIND
                or prior.book_code != book_code or prior.chapter != chapter.chapter_title):
                raise YoutubeDownloadError("youtube_storage_conflict")
            item = AudioFile(
                book_code=book_code, role_book_id=str(book.id),
                chapter=chapter.chapter_title, role="", actor_name="", kind=KIND,
                original_filename=filename, canonical_filename=filename, stored_key=key,
                mime_type="audio/mp4", size_bytes=size, md5=digest.hexdigest(),
                duration_seconds=duration, sample_rate=sample_rate, channels=channels, codec=codec[:40],
                location="local", verify_state="ok", verified_at=utcnow_naive(),
                mirror_state="", status="uploaded",
            )
            if prior is not None:
                item = prior
            else:
                db.add(item)
                db.flush()
            provenance = {
                "source": "youtube", "video_id": video_id, "source_url": url,
                "task_id": stable_id, "book_id": book.id, "chapter_id": chapter.id,
                "title": str(metadata.get("title") or "")[:1000],
                "uploader": str(metadata.get("uploader") or metadata.get("channel") or "")[:500],
                "channel_id": str(metadata.get("channel_id") or "")[:200],
                "declared_license": str(metadata.get("license") or "")[:500],
                "rights_confirmed": True, "rights_status": "owner_confirmed",
            }
            return DownloadResult(item, provenance, destination, owns_file)
    except BaseException:
        if final_path is not None:
            final_path.unlink(missing_ok=True)
        raise


perform = download_youtube_audio
