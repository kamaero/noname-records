"""Демо диктора: любое аудио или видео → MP3 192 кбит/с, не длиннее двух минут.

Длиннее двух минут демо никто не дослушивает, а каст листает десятки голосов подряд —
поэтому обрезаем, а не отказываем."""
from __future__ import annotations

import hashlib
import os
import subprocess

MAX_SECONDS = 120
BITRATE = "192k"


class ConvertError(RuntimeError):
    pass


def _duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, timeout=120)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def to_demo_mp3(source_path: str, target_path: str) -> dict:
    source_seconds = _duration(source_path)
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", source_path, "-vn", "-map", "0:a:0?", "-t", str(MAX_SECONDS),
         "-c:a", "libmp3lame", "-b:a", BITRATE, target_path],
        capture_output=True, timeout=900,
    )
    if result.returncode != 0 or not os.path.isfile(target_path) or os.path.getsize(target_path) < 1024:
        raise ConvertError(result.stderr.decode("utf-8", "ignore")[-300:] or "no_audio")
    with open(target_path, "rb") as handle:
        digest = hashlib.md5(handle.read()).hexdigest()
    return {"duration_seconds": _duration(target_path), "trimmed": source_seconds > MAX_SECONDS + 0.5,
            "size_bytes": os.path.getsize(target_path), "md5": digest}


