"""Имена персонажей: ключ сравнения, список псевдонимов, отсев эпитетов и титулов.

Общие для извлечения персонажей, канона автора, консилиума и сверки каста. Раньше
жили в `app/script_pipeline.py`.
"""
from __future__ import annotations

import re


_DISALLOWED_SPEAKER_MARKERS = (
    " или ",
    " и остальные",
    "кто-то",
    "кто то",
    "множественные голоса",
    "возможно",
    "внутренний монолог",
    "воспоминание",
    "голос ",
    "голос,",
)


def _is_valid_dialogue_speaker_name(name: str) -> bool:
    label = re.sub(r"\s+", " ", str(name or "").strip())
    if not label:
        return False
    upper = label.upper()
    if upper.startswith("UNSURE") or upper.startswith("UNKNOWN"):
        return False
    if len(label) > 120:
        return False
    if not re.search(r"[A-Za-zА-Яа-яЁё0-9]", label):
        return False

    lower = label.lower()
    if any(marker in lower for marker in _DISALLOWED_SPEAKER_MARKERS):
        return False

    tokens = _name_like_tokens(label)
    if not tokens or len(tokens) > 5:
        return False

    first = tokens[0].lower()
    if first in _GENERIC_ALIAS_WORDS and len(tokens) >= 2:
        return False

    # If model emitted all-lowercase descriptive nouns (e.g. "банка с соком"),
    # keep them out of CHAR_MEMORY; speaker labels should look like entities.
    if not any(re.search(r"[A-ZА-ЯЁ]", token) for token in tokens):
        return False

    return True


def _normalize_character_identity_key(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", "", str(value or "").strip().lower())


def _normalize_character_aliases(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,;\n]+", str(value or "")) if item.strip()]


_GENERIC_ALIAS_WORDS = {
    "мама", "папа", "мать", "отец", "дочь", "сын", "жена", "муж", "сестра", "брат", "тетя", "тётя",
    "дядя", "девочка", "мальчик", "дитя", "малышка", "малютка", "малявка", "мелкая", "кнопочка",
    "дорогая", "дорогой", "любимая", "любимый", "родная", "дружище", "госпожа", "господин", "леди",
    "хозяин", "хозяйка", "демон", "демоница", "колдун", "волшебник", "магиоз", "профессор", "доктор",
    "красавица", "невеста", "предательница", "трусиха", "подлая", "коварная", "шлюха", "говнюшка",
    "говняшка", "засранка", "дурында", "козявка", "котомка", "рукокрылое",
}


_POSSESSIVE_ALIAS_WORDS = {
    "мой", "моя", "моё", "мое", "мои", "твой", "твоя", "твоё", "твое", "твои", "наш", "наша",
    "наше", "наши", "ваш", "ваша", "ваше", "ваши", "самый", "самая", "самое", "самые",
}


_EPITHET_ENDINGS = ("ая", "яя", "ый", "ий", "ой", "ое", "ее", "ие", "ого", "ему", "ыми", "ыми")


# Generic role/title words. In Belozerov's world almost every demonlord carries a
# grand title shared across many characters (барон, Владыка, Купец, Тёмный
# Господин …). A title stays a legitimate alias (Вохпкогамкиуб = «Тёмный
# Господин»), but it must NEVER act as a merge bridge between two distinct
# canonicals — that is what transitively snowballed dozens of characters into one.
_TITLE_ROLE_WORDS = frozenset({
    "барон", "бароны", "владыка", "владычица", "господин", "госпожа", "князь",
    "княгиня", "барон", "баронесса", "банкир", "купец", "купчиха", "корчмарь", "палач",
    "рыцарь", "паладин", "шут", "шутник", "балаганщик", "дама", "принц", "принцесса",
    "король", "королева", "царь", "царица", "мессир", "сэр", "лорд", "мастер", "матерь",
    "отец", "мать", "дядюшка", "дядя", "тётя", "тетя", "ростовщик", "бухгалтер",
    "центурион", "вексилларий", "учитель", "искуситель", "охотница", "невеста",
    "блудница", "сумрак", "хаос", "бушук", "хилакток", "ларитра", "младенец", "корчмарка",
    # decorating adjectives that ride on titles
    "тёмный", "темный", "тёмная", "темная", "тёмное", "темное", "великий", "великая",
    "величайший", "омерзительный", "зловещий", "полумракский", "полумракская",
    "царственный", "царственная", "клубящийся", "крылатый", "полумрака", "тьмы",
    "пороков", "зла", "снов",
})


def _is_pure_title_name(value: str) -> bool:
    """True if every word is a generic role/title (no proper name) — e.g.
    «Тёмный Господин», «барон». Such a name must not bridge a merge."""
    words = re.findall(r"[а-яёa-z]+", str(value or "").lower())
    return bool(words) and all(word in _TITLE_ROLE_WORDS for word in words)


def _name_like_tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[A-Za-zА-Яа-яЁё0-9-]+", str(value or "").strip()) if token]


def _looks_like_epithet_token(token: str) -> bool:
    lower = str(token or "").strip().lower()
    return len(lower) >= 4 and lower.endswith(_EPITHET_ENDINGS)


def _filter_extracted_aliases(canonical_name: str, aliases: list[str]) -> list[str]:
    canonical_tokens = {
        _normalize_character_identity_key(token)
        for token in _name_like_tokens(canonical_name)
        if _normalize_character_identity_key(token)
    }
    canonical_key = _normalize_character_identity_key(canonical_name)
    seen: set[str] = set()
    cleaned: list[str] = []

    for raw_alias in aliases:
        alias = re.sub(r"\s+", " ", str(raw_alias or "").strip())
        if not alias:
            continue
        alias_key = _normalize_character_identity_key(alias)
        if not alias_key or alias_key == canonical_key or alias_key in seen:
            continue

        tokens = _name_like_tokens(alias)
        if not tokens:
            continue
        lower_tokens = [token.lower() for token in tokens]
        token_keys = {_normalize_character_identity_key(token) for token in tokens if _normalize_character_identity_key(token)}
        overlaps_canonical = bool(token_keys & canonical_tokens)

        if len(tokens) > 4:
            continue
        if any(token in _POSSESSIVE_ALIAS_WORDS for token in lower_tokens):
            continue
        if len(tokens) == 1 and lower_tokens[0] in _GENERIC_ALIAS_WORDS and not overlaps_canonical:
            continue
        if len(tokens) >= 2 and all(token == token.lower() for token in tokens) and not overlaps_canonical:
            continue
        if overlaps_canonical:
            non_canonical_tokens = [
                token for token in tokens if _normalize_character_identity_key(token) not in canonical_tokens
            ]
            if any(_looks_like_epithet_token(token) for token in non_canonical_tokens):
                continue
        elif len(tokens) >= 3:
            continue

        seen.add(alias_key)
        cleaned.append(alias)
    return cleaned
