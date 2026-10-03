"""Что за файл нам прислали — спрашиваем у самого файла, а не у его имени.

До сих пор о загруженном мы знали только размер в байтах. Размер не отличает
пятнадцать минут речи от пятнадцати минут тишины, не говорит, что диктор писал в стерео
на 22 кГц, и не даёт длительности — а она нужна и смете, и описи главы, и любой будущей
сборке в монтажке.

`ffprobe` отвечает за треть секунды даже на файле в восемьдесят мегабайт, поэтому
спрашиваем прямо при загрузке. Уровни (пик, шумовая полка) сюда не входят нарочно: они
требуют декодировать файл целиком, а к тому времени, когда они понадобятся, его всё
равно будет декодировать распознавание речи.

Ответ — предупреждение, а не запрет: что делать с файлом, записанным не так, решает
студия, а не парсер. Так же, как с несовпадением роли в имени файла.
"""
from __future__ import annotations

import json
import shutil
import subprocess

#: ниже этого студия не пишет — на 22 кГц голос звучит телефоном
MIN_SAMPLE_RATE = 44100
#: дубль короче — почти наверняка обрывок или тишина
MIN_DURATION_SECONDS = 1.0

_FIELDS = "format=duration:stream=codec_name,sample_rate,channels,bits_per_sample"


def probe_audio_file(path: str) -> dict:
    """Длительность и формат. Пустой словарь — файл не прочитался, и это тоже ответ."""
    if not shutil.which("ffprobe"):
        return {}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", _FIELDS, "-of", "json", str(path)],
            capture_output=True, timeout=30, check=False,
        )
        payload = json.loads(result.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}

    streams = payload.get("streams") or []
    if not streams:
        return {}
    stream = streams[0]
    try:
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "duration_seconds": round(duration, 3),
        "sample_rate": int(stream.get("sample_rate") or 0),
        "channels": int(stream.get("channels") or 0),
        "codec": str(stream.get("codec_name") or ""),
        "bit_depth": int(stream.get("bits_per_sample") or 0),
    }


def probe_warnings(info: dict) -> list[str]:
    """Что стоит сказать диктору про его файл. Про нечитаемый файл — ничего: молчание
    парсера не должно выглядеть как одобрение, но и гадать он не станет."""
    if not info:
        return []
    warnings: list[str] = []
    if 0 < int(info.get("sample_rate") or 0) < MIN_SAMPLE_RATE:
        warnings.append("low_sample_rate")
    if int(info.get("channels") or 0) > 1:
        warnings.append("stereo")
    if float(info.get("duration_seconds") or 0.0) < MIN_DURATION_SECONDS:
        warnings.append("too_short")
    codec = str(info.get("codec") or "")
    if codec and not codec.startswith("pcm"):
        warnings.append("compressed")
    return warnings
