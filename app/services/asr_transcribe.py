"""Отправить запись на распознавание — и не упереться в предел на размер файла.

У whisper-1 файл не длиннее 25 мегабайт, а дубли у нас по восемьдесят: пятнадцать минут
несжатого WAV. Резать файл на куски — значит резать по живому слову и потом сшивать
тайминги; сжать его в моно 16 кГц дешевле и надёжнее. Модель всё равно понижает частоту
до шестнадцати килогерц, так что мы ничего не теряем: те же пятнадцать минут занимают
около четырёх мегабайт вместо восьмидесяти. Часовая запись уложится в предел с запасом.

Тайминги — то, ради чего всё затевалось: без них не расставить ни маркеры в Audition,
ни клипы в сессии. Поэтому просим `verbose_json` и пословную разбивку.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import requests

OPENAI_BASE_URL = "https://api.openai.com/v1"
PATH = "/audio/transcriptions"
#: предел загрузки у сервиса
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
#: частота, до которой модель всё равно понижает звук
ASR_SAMPLE_RATE = 16000
ASR_BITRATE = "32k"


class AsrError(RuntimeError):
    """Распознавание не состоялось. Текст ошибки — код, а не фраза: его читает лог."""


def compress_for_asr(source_path: str, target_path: str) -> str:
    """Моно, 16 кГц, mp3 — то, что модель услышит ровно так же, но в двадцать раз легче."""
    if not shutil.which("ffmpeg"):
        raise AsrError("ffmpeg_missing")
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(source_path),
            "-ac", "1", "-ar", str(ASR_SAMPLE_RATE), "-b:a", ASR_BITRATE,
            str(target_path),
        ],
        capture_output=True, timeout=900, check=False,
    )
    if result.returncode != 0 or not os.path.isfile(target_path) or os.path.getsize(target_path) == 0:
        raise AsrError("compress_failed")
    return str(target_path)


def parse_transcription(payload: dict) -> dict:
    """Ответ сервиса — в наш вид: сегменты со временем, у каждого свои слова.

    Слова приходят отдельным списком на весь файл; развешиваем их по сегментам, чтобы
    выравниванию не пришлось знать про эту особенность формата.
    """
    segments_in = (payload or {}).get("segments") or []
    words_in = (payload or {}).get("words") or []
    segments = []
    for item in segments_in:
        start = float(item.get("start") or 0.0)
        end = float(item.get("end") or start)
        segments.append({
            "start": start,
            "end": end,
            "text": str(item.get("text") or "").strip(),
            "words": [
                {"word": str(word.get("word") or ""), "start": float(word.get("start") or 0.0), "end": float(word.get("end") or 0.0)}
                for word in words_in
                if start <= float(word.get("start") or 0.0) < end
            ],
        })
    return {"text": str((payload or {}).get("text") or "").strip(), "segments": segments}


def transcribe_file(
    path: str,
    *,
    api_key: str,
    model: str,
    language: str = "ru",
    prompt: str = "",
    base_url: str = OPENAI_BASE_URL,
    timeout: int = 600,
) -> dict:
    """Распознать уже сжатый файл. Ошибки — свои, чтобы вызывающий не разбирал чужие.

    Адрес настраивается: RouterAI совместим с OpenAI по формату, но живёт по другому
    адресу и берёт свои модели. Один код на обоих.
    """
    if not str(api_key or "").strip():
        raise AsrError("no_api_key")
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    if size > MAX_UPLOAD_BYTES:
        # Проверяем до сети: отправить и получить отказ стоит времени и трафика.
        raise AsrError("file_too_large")

    data = {
        "model": str(model or "whisper-1"),
        "response_format": "verbose_json",
        "timestamp_granularities[]": ["segment", "word"],
    }
    if str(language or "").strip():
        data["language"] = str(language).strip()
    if str(prompt or "").strip():
        data["prompt"] = str(prompt).strip()

    with open(path, "rb") as handle:
        response = requests.post(
            f"{str(base_url or OPENAI_BASE_URL).rstrip('/')}{PATH}",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (os.path.basename(path), handle)},
            data=data,
            timeout=timeout,
        )
    if response.status_code >= 400:
        raise AsrError(f"asr_http_{response.status_code}")
    try:
        return parse_transcription(response.json())
    except ValueError as exc:
        raise AsrError("asr_bad_json") from exc
