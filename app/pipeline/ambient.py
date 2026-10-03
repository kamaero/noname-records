"""Эмбиент по главе: чистые правила без сети и без БД.

Длина трека, имя файла и промпт для Opus — три независимых чистых расчёта; сеть
(ElevenLabs) и запись (движок, таблица `ambient_tracks`) — в другом слое.
"""
from __future__ import annotations

import json
import re

MIN_TRACK_SECONDS = 180
MAX_TRACK_SECONDS = 600
_MAX_STEM_LENGTH = 100
_FORBIDDEN_CHARS = re.compile(r'[/\\:*?"<>|]')
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")
#: имя сцены без места — то же, что у маркера сцены в `.sesx`
NO_PLACE = "без места"


def track_seconds(scene_seconds: float) -> int:
    """Длина трека = длина сцены на таймлайне, зажатая в [180, 600] с, целые секунды."""
    try:
        value = float(scene_seconds or 0)
    except (TypeError, ValueError):
        value = 0.0
    clamped = max(MIN_TRACK_SECONDS, min(MAX_TRACK_SECONDS, value))
    return int(round(clamped))


def clean_file_stem(text: str) -> str:
    """Своя очистка имени: убрать запрещённые/управляющие символы, схлопнуть пробелы,
    «ё» и остальные буквы не трогать, длина ≤ 100."""
    cleaned = _FORBIDDEN_CHARS.sub("", str(text or ""))
    cleaned = _CONTROL_CHARS.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    return cleaned[:_MAX_STEM_LENGTH]


def ambient_file_names(chapter_index: int, place_names: list[str]) -> list[str]:
    """Имена файлов по порядку сцен: `Г{N}_Эмбиент - {место}.mp3`, повтор места в
    главе получает ` 2`, ` 3`… перед расширением (счёт — по очищенному имени места).
    Места нет или от него после очистки ничего не осталось — «без места», как в маркере."""
    seen: dict[str, int] = {}
    names: list[str] = []
    for place_name in place_names:
        stem = clean_file_stem(place_name) or NO_PLACE
        seen[stem] = seen.get(stem, 0) + 1
        occurrence = seen[stem]
        suffix = f" {occurrence}" if occurrence > 1 else ""
        names.append(f"Г{chapter_index}_Эмбиент - {stem}{suffix}.mp3")
    return names


PROMPT_SYSTEM = """Ты составляешь промпт для генерации инструментальной музыки ElevenLabs \
(model_id=music_v2) — фоновую подложку под сцену аудиокниги, звучащую под живым чтением \
рассказчика.

Требования к треку: инструментальный, без вокала, без резких ударных акцентов и громких \
кульминаций, ровная динамика без резких скачков громкости — чтеца нельзя заглушать.

Тебе дают: место и его описание, время суток, погоду, звуковой фон сцены, настроение, \
музыкальные запросы, начало текста сцены, требуемую длину трека в секундах и краткие \
описания прежних треков этой книги (ведущие инструменты · фактура · темп, по строке на \
трек). Каждый новый трек должен звучать иначе прежних — не повторяй их ведущие инструменты \
и фактуру.

Ответь по-английски:
- prompt — промпт для ElevenLabs music_v2, 40-80 слов: инструменты, настроение, темп, \
динамика;
- summary — одна английская строка вида "instruments · texture · tempo" для следующего \
вызова."""

PROMPT_SCHEMA = {
    "type": "object",
    "properties": {
        "prompt": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["prompt", "summary"],
}


def prompt_user(scene: dict, text_excerpt: str, seconds: int, previous: list[str]) -> str:
    """Пользовательский промпт Opus: разметка сцены + начало текста + длина + прежние summary."""
    scene = scene or {}
    place = scene.get("place") or {}
    place_name = str(place.get("name") or "").strip()
    place_description = str(place.get("description") or "").strip()
    music_queries = ", ".join(str(q).strip() for q in (scene.get("music_queries") or []) if str(q).strip())
    previous_block = "\n".join(f"- {line}" for line in (previous or []) if str(line).strip())
    return (
        f"Место: {place_name} — {place_description}\n"
        f"Время суток: {scene.get('time_of_day') or ''}. Погода: {scene.get('weather') or ''}.\n"
        f"Фон сцены: {scene.get('ambience') or ''}.\n"
        f"Настроение: {scene.get('mood') or ''}.\n"
        f"Музыкальные запросы: {music_queries or '(нет)'}\n\n"
        f"Начало текста сцены:\n{text_excerpt or ''}\n\n"
        f"Длина трека: {int(seconds)} секунд.\n\n"
        f"Прежние треки книги (не повторять инструменты и фактуру):\n"
        f"{previous_block or '(прежних треков ещё нет)'}"
    )


def parse_prompt(answer) -> tuple[str, str]:
    """Разбирает ответ модели `{"prompt": ..., "summary": ...}`.

    Принимает как уже разобранный словарь, так и сырую JSON-строку (обе формы
    встречаются у вызывающего кода). Пустой или битый ответ — `ValueError`.
    """
    data = answer
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        stripped = data.strip()
        if not stripped:
            raise ValueError("пустой ответ модели")
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError("не удалось разобрать ответ модели как JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("ответ модели не объект")
    prompt = str(data.get("prompt") or "").strip()
    summary = str(data.get("summary") or "").strip()
    if not prompt or not summary:
        raise ValueError("в ответе модели нет prompt или summary")
    return prompt, summary
