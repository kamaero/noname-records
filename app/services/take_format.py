"""Дубль идёт на сведение: MP3 годится для пробы, но не должен стать исходником роли."""
from __future__ import annotations

from pathlib import Path

REQUIRED = "Дубли принимаются только в WAV или FLAC. MP3 и голосовые можно присылать для проб. Пришлите исходную запись в WAV или FLAC."


def allowed_filename(filename: str, *, kind: str = "") -> bool:
    return kind != "voice" and Path(str(filename or "")).suffix.lower() in {".wav", ".flac"}


def allowed_audio(path: str, probe: dict) -> bool:
    """Расширение и MIME присылает клиент: переименованный MP3 нельзя пропустить к ASR.

    В WAV допускаем PCM, в FLAC — его собственный кодек. Проверяем и контейнер: PCM
    в AIFF и FLAC в Ogg тоже не являются запрошенными WAV/FLAC.
    """
    with open(path, "rb") as handle:
        header = handle.read(12)
    codec = str(probe.get("codec") or "")
    wav = header[:4] in {b"RIFF", b"RF64", b"BW64"} and header[8:12] == b"WAVE"
    return (wav and codec.startswith("pcm_")) or (header[:4] == b"fLaC" and codec == "flac")
