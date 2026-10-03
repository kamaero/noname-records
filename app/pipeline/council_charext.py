"""char_extraction-specific council contracts: bounded cast schema + prompts.

Pure strings/dicts — no network, no DB.
"""
from __future__ import annotations

COUNCIL_CAST_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                },
                "required": ["canonical"],
            },
        }
    },
    "required": ["characters"],
}

_PARTICIPANT_SYSTEM = (
    "Ты извлекаешь ПОЛНЫЙ каст книги для аудио-постановки. Верни СТРОГО JSON по схеме, "
    "без прозы и пояснений вне JSON. Для каждого персонажа: канон/основное имя, список "
    "алиасов (как его называют в тексте) и до 3 коротких evidence-сниппетов. НЕ тащи в "
    "алиасы семейные роли, обращения и декоративные эпитеты, если это не устойчивый идентификатор."
)

_ARBITER_SYSTEM = (
    "Ты АРБИТР совета. Тебе дают два независимых списка каста. Сведи их в ОДИН: слей "
    "дубликаты по смыслу, отсей ложные/ситуативные алиасы, оставь только устойчивые "
    "идентификаторы. Верни СТРОГО JSON по той же схеме. Только решение, без прозы, не "
    "переписывай evidence-тексты."
)


def build_participant_prompt(book_text: str) -> tuple[str, str]:
    user = "Текст книги (извлеки каст):\n" + book_text
    return _PARTICIPANT_SYSTEM, user


def build_arbiter_prompt(cast_json_a: str, cast_json_b: str) -> tuple[str, str]:
    user = (
        "Список каста участника A:\n" + cast_json_a + "\n\n"
        "Список каста участника B:\n" + cast_json_b + "\n\n"
        "Сведи в один согласованный список каста."
    )
    return _ARBITER_SYSTEM, user


_REFINE_SYSTEM = (
    "Ты АРБИТР. Тебе дают ОДИН список каста, собранный по главам (recall). Почисти его: "
    "слей дубликаты-идентичности под один каноник, отсей ложные/ситуативные алиасы "
    "(семейные роли, обращения, декоративные эпитеты), оставь только устойчивые "
    "идентификаторы. НЕ удаляй реальных персонажей. "
    "КРИТИЧНО: алиас — это как ИНАЧЕ называют ОДНОГО И ТОГО ЖЕ персонажа (прозвище, титул, "
    "сокращение). НЕ объединяй под один каноник РАЗНЫХ персонажей: если у двух записей "
    "разные собственные имена/фамилии (напр. «Сатухух Гарсиаваль» и «Дгарнин»), это РАЗНЫЕ "
    "персонажи — НЕ делай одного алиасом другого. Сомневаешься — оставь раздельно. "
    "Верни СТРОГО JSON по той же схеме, только решение, без прозы."
)


def build_refine_prompt(cast_json: str) -> tuple[str, str]:
    user = "Список каста (собран по главам, почисти и слей дубли):\n" + cast_json
    return _REFINE_SYSTEM, user
