"""Распознавание через Azure — второй провайдер за тем же швом.

Наверх ничего не меняется: выравнивание получает те же сегменты со временем, что и от
whisper, и знать не знает, кто их произвёл. Ради этого шов и заводился.

У Azure два отличия, которые переводятся здесь. Время он считает в миллисекундах и
отдаёт длительностью, а не концом отрезка. И он принимает список фраз — подсказку,
каких слов ждать: для книги, полной выдуманных имён вроде «Бимькмолепуса», это не
украшение, а разница между «реплика найдена» и «реплика пропущена».

Взят Fast Transcription: файл уходит прямо в запросе, до 500 МБ и пяти часов, а ответ
приходит сразу. Batch дешевле вдвое, но требует, чтобы Azure сам дотянулся до аудио —
то есть блоб-хранилища и ссылок с подписью; для наших объёмов это лишний узел.
"""
from __future__ import annotations

import json
import os

import requests

from app.services.asr_transcribe import AsrError

API_VERSION = "2025-10-15"
PATH = "/speechtotext/transcriptions:transcribe"
#: предел на список фраз в документации не оговорён — держимся заведомо безопасного числа
MAX_PHRASES = 500
#: предел сервиса на файл
MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def build_definition(*, locale: str, phrases: list[str] | None = None) -> dict:
    """Что сказать сервису до того, как он услышит первое слово."""
    definition: dict = {"locales": [str(locale or "ru-RU")]}
    seen: list[str] = []
    for phrase in phrases or []:
        text = " ".join(str(phrase or "").split())
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= MAX_PHRASES:
            break
    if seen:
        # Пустой список — не то же, что отсутствие списка: сервис вправе поспорить.
        definition["phraseList"] = {"phrases": seen}
    return definition


def parse_azure_transcription(payload: dict) -> dict:
    """Ответ Azure — в наш вид: секунды вместо миллисекунд, конец вместо длительности."""
    phrases = (payload or {}).get("phrases") or []
    combined = (payload or {}).get("combinedPhrases") or []
    segments = []
    for phrase in phrases:
        start = float(phrase.get("offsetMilliseconds") or 0) / 1000.0
        end = start + float(phrase.get("durationMilliseconds") or 0) / 1000.0
        segments.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": str(phrase.get("text") or "").strip(),
            "words": [
                {
                    "word": str(word.get("text") or ""),
                    "start": round(float(word.get("offsetMilliseconds") or 0) / 1000.0, 3),
                    "end": round((float(word.get("offsetMilliseconds") or 0) + float(word.get("durationMilliseconds") or 0)) / 1000.0, 3),
                }
                for word in phrase.get("words") or []
            ],
        })
    text = " ".join(str(item.get("text") or "").strip() for item in combined).strip()
    return {"text": text, "segments": segments}


def transcribe_file_azure(
    path: str,
    *,
    key: str,
    endpoint: str,
    locale: str = "ru-RU",
    phrases: list[str] | None = None,
    timeout: int = 900,
) -> dict:
    """Распознать файл. Ошибки — те же, что у первого провайдера: вызывающий один."""
    if not str(key or "").strip():
        raise AsrError("no_api_key")
    if not str(endpoint or "").strip():
        raise AsrError("no_endpoint")
    size = os.path.getsize(path) if os.path.isfile(path) else 0
    if size > MAX_UPLOAD_BYTES:
        raise AsrError("file_too_large")

    url = f"{str(endpoint).rstrip('/')}{PATH}?api-version={API_VERSION}"
    definition = json.dumps(build_definition(locale=locale, phrases=phrases), ensure_ascii=False)
    with open(path, "rb") as handle:
        response = requests.post(
            url,
            headers={"Ocp-Apim-Subscription-Key": str(key)},
            files={"audio": (os.path.basename(path), handle)},
            data={"definition": definition},
            timeout=timeout,
        )
    if response.status_code >= 400:
        raise AsrError(f"asr_http_{response.status_code}")
    try:
        return parse_azure_transcription(response.json())
    except ValueError as exc:
        raise AsrError("asr_bad_json") from exc
