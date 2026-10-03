"""Звуковая разметка: промпт, разбор и проверка ответа модели. Чистые функции.

Модели не верим на слово: абзац должен существовать, сцены идти по порядку, а цитата
звука — дословно стоять в тексте. Иначе «хлопнула дверь» повиснет там, где двери нет.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SYSTEM = """Ты — звукорежиссёр аудиокниги. Тебе дают главу с пронумерованными абзацами и
список мест, уже встречавшихся в книге. Размечаешь главу для сведения:

1. СЦЕНЫ — отрезки с одним местом и одной звуковой средой. Сцена начинается там, где
   меняется место, время или обстановка. У каждой: номер первого абзаца, место (id из
   списка, если это то же место; иначе новое — имя, короткое описание, 2-3 английских
   запроса для поиска фоновой атмосферы), время суток, погода, фон (что звучит вокруг),
   настроение и 1-3 английских запроса для поиска музыки.
2. ПЕРЕХОДЫ — где фон должен смениться или затихнуть внутри или между сценами: флешбэк,
   сон, видение, резкая смена места, внезапная тишина. Номер абзаца и что происходит.
3. ЗНАЧИМЫЕ ЗВУКИ — только те, что сами по себе событие или акцент: удар грома, взрыв,
   колокол, хлопок двери в тишине, крик за стеной. Не отмечай шаги, шорохи, звон посуды,
   дыхание и прочий бытовой фон. Обычно 5-15 на главу, бывает меньше. У каждого: номер
   абзаца, ДОСЛОВНАЯ цитата из этого абзаца (2-6 слов, как в тексте), описание по-русски
   и 1-3 английских запроса для библиотек звуков.

Описания — по-русски, коротко. Запросы — по-английски, как их вводят в Epidemic Sound
или freesound. Если место из списка — верни его id, не заводи двойника."""

_QUERIES = {"type": "array", "items": {"type": "string"}}
SCHEMA = {
    "type": "object",
    "properties": {
        "scenes": {"type": "array", "items": {"type": "object", "properties": {
            "start": {"type": "integer"},
            "place": {"type": "object", "properties": {
                "id": {"type": "string"}, "name": {"type": "string"},
                "description": {"type": "string"}, "ambience_queries": _QUERIES},
                "required": ["id", "name", "description", "ambience_queries"]},
            "time_of_day": {"type": "string"}, "weather": {"type": "string"},
            "ambience": {"type": "string"}, "mood": {"type": "string"}, "music_queries": _QUERIES},
            "required": ["start", "place", "time_of_day", "weather", "ambience", "mood", "music_queries"]}},
        "transitions": {"type": "array", "items": {"type": "object", "properties": {
            "at": {"type": "integer"}, "what": {"type": "string"}}, "required": ["at", "what"]}},
        "sounds": {"type": "array", "items": {"type": "object", "properties": {
            "at": {"type": "integer"}, "quote": {"type": "string"},
            "description": {"type": "string"}, "queries": _QUERIES},
            "required": ["at", "quote", "description", "queries"]}},
    },
    "required": ["scenes", "transitions", "sounds"],
}

MERGE_SYSTEM = """Тебе дают список мест одной книги. Найди пары, которые почти наверняка
одно и то же место, названное по-разному («таверна у Ворот» и «кабак у ворот»). Не
склеивай просто похожие места (две разные таверны). Верни только такие пары и причину."""

MERGE_SCHEMA = {
    "type": "object",
    "properties": {"pairs": {"type": "array", "items": {"type": "object", "properties": {
        "a": {"type": "string"}, "b": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["a", "b", "reason"]}}},
    "required": ["pairs"],
}


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower().replace("ё", "е")).strip()


def _queries(value) -> list[str]:
    return [str(q).strip() for q in (value or []) if str(q).strip()][:3]


def chapter_prompt(paragraphs: list[tuple[int, str]], places: list[dict]) -> str:
    known = "\n".join(f"{p['id']} — {p['name']}: {p['description']}" for p in places) or "(пока нет)"
    body = "\n".join(f"[{n}] {text}" for n, text in paragraphs)
    return f"Места книги:\n{known}\n\nГлава:\n{body}"


def merge_prompt(places: list[dict]) -> str:
    return "\n".join(f"{p['id']} — {p['name']}: {p['description']}" for p in places)


def find_quote(paragraphs: dict[int, str], ordinal: int, quote: str, reach: int = 2) -> int | None:
    """Абзац, где цитата стоит дословно: сначала свой, потом соседи по близости."""
    needle = norm(quote)
    if not needle:
        return None
    for shift in [0] + [s for d in range(1, reach + 1) for s in (-d, d)]:
        text = paragraphs.get(ordinal + shift)
        if text is not None and needle in norm(text):
            return ordinal + shift
    return None


@dataclass
class Checked:
    scenes: list[dict] = field(default_factory=list)
    transitions: list[dict] = field(default_factory=list)
    sounds: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)


def validate(paragraphs: list[tuple[int, str]], answer: dict, known_place_ids: set[str]) -> Checked:
    texts = dict(paragraphs)
    first = min(texts) if texts else 0
    out = Checked()

    scenes = []
    for item in (answer or {}).get("scenes") or []:
        ordinal = int(item.get("start", -1))
        if ordinal not in texts:
            out.dropped.append({"kind": "scene", "at": ordinal, "reason": "no_paragraph"})
            continue
        place = item.get("place") or {}
        place_id = str(place.get("id") or "")
        scenes.append({
            "ordinal": ordinal,
            "place": {"id": place_id if place_id in known_place_ids else "",
                      "name": str(place.get("name") or "").strip(),
                      "description": str(place.get("description") or "").strip(),
                      "ambience_queries": _queries(place.get("ambience_queries"))},
            **{key: str(item.get(key) or "").strip() for key in ("time_of_day", "weather", "ambience", "mood")},
            "music_queries": _queries(item.get("music_queries")),
        })
    scenes.sort(key=lambda s: s["ordinal"])
    if scenes and scenes[0]["ordinal"] != first:
        scenes[0]["ordinal"] = first  # глава всегда в какой-то сцене
    seen: set[int] = set()
    out.scenes = [s for s in scenes if not (s["ordinal"] in seen or seen.add(s["ordinal"]))]

    for item in (answer or {}).get("transitions") or []:
        ordinal = int(item.get("at", -1))
        if ordinal not in texts:
            out.dropped.append({"kind": "transition", "at": ordinal, "reason": "no_paragraph"})
            continue
        out.transitions.append({"ordinal": ordinal, "what": str(item.get("what") or "").strip()})

    for item in (answer or {}).get("sounds") or []:
        ordinal = int(item.get("at", -1))
        quote = str(item.get("quote") or "").strip()
        if ordinal not in texts:
            out.dropped.append({"kind": "sound", "at": ordinal, "quote": quote, "reason": "no_paragraph"})
            continue
        found = find_quote(texts, ordinal, quote)
        if found is None:
            out.dropped.append({"kind": "sound", "at": ordinal, "quote": quote, "reason": "quote_not_found"})
            continue
        out.sounds.append({"ordinal": found, "quote": quote, "description": str(item.get("description") or "").strip(),
                           "queries": _queries(item.get("queries"))})
    return out
