"""Промпты и разбор ответов консилиума — перенос откалиброванных скриптов 2026-09-12.

Смысл формулировок не меняется: калибровка на 10 главах показала, что попытка объяснить
чтецу условности студии прозой обрушила согласие с человеком 58.7 % → 33.7 %. Любая правка
текста здесь — новая калибровка, а не рефакторинг.
"""
from __future__ import annotations

NARRATOR = "Рассказчик"
UNSURE = "UNSURE"
READER_CHUNK = 110
READER_CONTEXT = 25
ARBITER_WINDOW = 6

READER_SYSTEM = """Ты определяешь, КТО произносит прямую речь в каждом абзаце главы книги.

Тебе дают список персонажей главы и пронумерованные абзацы. По каждому абзацу верни одно
имя:
- точное имя из списка персонажей — если в абзаце звучит прямая речь этого персонажа;
- «Рассказчик» — если это авторская речь, описание, мысли от третьего лица, слова автора
  внутри чужой реплики («— сказал он»), или в абзаце вообще нет прямой речи;
- «UNSURE» — если по тексту честно нельзя понять, кто говорит.

Определяй по словам автора, по обращениям, по чередованию реплик в диалоге, по смыслу
сцены. Имена возвращай ровно в том написании, что в списке персонажей: если в тексте
персонаж назван прозвищем или титулом, верни его имя из списка.

Отвечай за КАЖДЫЙ поданный абзац, ровно один раз за каждый, без пропусков и без лишних.

«UNSURE» — нормальный ответ, а не поражение. По этой разметке будут записывать живых
актёров: догадка, выданная за уверенность, стоит дороже честного незнания."""

READER_SYSTEM_EN = """Identify WHO speaks the direct dialogue in each paragraph of an English book chapter.

You receive a chapter cast and numbered paragraphs. Return exactly one name per paragraph:
- an exact cast name if that character speaks directly;
- the internal token "Рассказчик" for narration, description, third-person thoughts, author tags such as "Alice said", or a paragraph without direct speech;
- "UNSURE" when the text does not identify the speaker honestly.

English dialogue may use straight or curly double or single quotation marks. Apostrophes in contractions and possessives are not dialogue boundaries. Identify the speaker from author tags, forms of address, dialogue turns and scene context. "Alice said to Bob" identifies Alice, not Bob. Return canonical cast names rather than nicknames, titles or alternative spellings.
First-person narrative prose still belongs to "Рассказчик"; only the character's direct dialogue belongs to the character. Never translate this internal narrator token or the source text.

Answer every supplied paragraph exactly once, with no omissions or extras. "UNSURE" is a valid answer: a confident guess assigns a real actor the wrong voice."""

READER_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "speaker": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["id", "speaker", "confidence"],
            },
        }
    },
    "required": ["lines"],
}

ARBITER_SYSTEM = """Ты разбираешь спорное место в разметке книги для многоголосой аудиокниги.

Тебе дают кусок текста с номерами абзацев и несколько версий того, кто произносит реплику
в спорном абзаце. Чья версия чья — не сказано, и это неважно: важно, что говорит текст.
«Рассказчик» — это авторское повествование, а не персонаж.

Верни говорящего и ДОКАЗАТЕЛЬСТВО. Говорящий — имя из списка персонажей сцены или
«Рассказчик». Доказательство — дословная строка из указанного тобой абзаца, не пересказ,
не длиннее 200 знаков. Цитата проверяется машинно: если её нет в тексте дословно, ответ
отбрасывается целиком.

evidence_kind:
- cue_named — авторские слова называют говорящего («— сказал Гамук»);
- address_by_name — собеседник обращается к нему по имени в соседней реплике;
- alternation — чередование ходов в диалоге, где участников двое и оба названы;
- context_only — ты понял по смыслу, но прямого указания в тексте нет.

context_only никогда ничего не закрывает — это честное «не доказано». Ставь его смело:
по этой разметке пишут живых актёров, и догадка, выданная за доказательство, дороже
признания незнания."""

ARBITER_SYSTEM_EN = """Review a disputed speaker attribution in an English multi-voice audiobook.

You receive numbered paragraphs and competing speaker labels. Their source is deliberately anonymous. Decide from the text. The internal token "Рассказчик" means narration, not a character; never translate it in the response.

Return an exact scene cast name or "Рассказчик", plus evidence: a verbatim quote from the paragraph you identify, at most 200 characters. A quote that does not occur in the original text is rejected. Do not translate or paraphrase the quote.

evidence_kind:
- cue_named: an author tag explicitly names the speaker, for example "Alice said" or "said Alice". In "Alice said to Bob", Alice speaks and Bob is the addressee.
- address_by_name: a neighbour addresses the character by name in the next dialogue turn.
- alternation: two identified characters alternate dialogue turns.
- context_only: an interpretation inferred from context without direct textual evidence.

English dialogue can use double or single quotation marks, including curly quotes, with intervening author tags. Apostrophes do not start dialogue. Narration and author tags belong to "Рассказчик". First-person narrative prose is also narration.
context_only never resolves a finding. Use it whenever direct evidence is absent; a plausible interpretation is not proof. Write reason in English, preserve names and return the schema exactly."""

ARBITER_SCHEMA = {
    "type": "object",
    "properties": {
        "speaker": {"type": "string"},
        "evidence_kind": {"type": "string",
                          "enum": ["cue_named", "address_by_name", "alternation", "context_only"]},
        "evidence_para": {"type": "integer"},
        "evidence_quote": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["speaker", "evidence_kind", "evidence_para", "evidence_quote", "reason"],
}


def normalize_id(raw) -> int:
    """«#00017», «00017», «17» — один и тот же абзац. Разные семейства пишут по-разному."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return int(digits) if digits else -1


def as_pair(line) -> tuple[int, str] | None:
    """Элемент ответа → (номер абзаца, имя). Терпит обе формы, которые дают модели.

    Строгая схема гарантирует ключи объекта, но не то, что элементом массива будет объект:
    на главе 25 один из чтецов вернул массив СТРОК вида «#00123 Дгарнин» и формально схему
    не нарушил.
    """
    if isinstance(line, dict):
        number = normalize_id(line.get("id"))
        who = str(line.get("speaker") or "").strip()
        return (number, who) if number >= 0 and who else None
    if isinstance(line, str):
        parts = line.strip().split(maxsplit=1)
        if not parts:
            return None
        number = normalize_id(parts[0])
        who = parts[1].lstrip("→:-— ").strip() if len(parts) > 1 else ""
        return (number, who) if number >= 0 and who else None
    return None


def reader_user_prompt(cast_lines: list[str], paragraphs: list[tuple[int, str]],
                       already: list[tuple[int, str]], *, language: str = "ru") -> str:
    if language == "en":
        context = ("CONTEXT — your previous decisions in this chapter (do not answer for them):\n"
                   + "\n".join(f"#{n:05d} → {who}" for n, who in already[-READER_CONTEXT:]) + "\n\n") if already else ""
        return ("CHAPTER CAST:\n" + "\n".join(cast_lines) + "\n\n" + context
                + "PARAGRAPHS:\n" + "\n".join(f"#{n:05d} {text}" for n, text in paragraphs))
    context = ""
    if already:
        tail = already[-READER_CONTEXT:]
        context = ("КОНТЕКСТ — твои же предыдущие решения по этой главе (за них не отвечай):\n"
                   + "\n".join(f"#{n:05d} → {who}" for n, who in tail) + "\n\n")
    return ("ПЕРСОНАЖИ ГЛАВЫ:\n" + "\n".join(cast_lines) + "\n\n" + context
            + "АБЗАЦЫ:\n" + "\n".join(f"#{n:05d} {text}" for n, text in paragraphs))


def arbiter_user_prompt(scene: list[str], window: list[tuple[int, str]], ordinal: int,
                        versions: list[str], *, language: str = "ru") -> str:
    if language == "en":
        return ("SCENE CAST:\n" + "\n".join(scene)
                + "\n\nTEXT:\n" + "\n".join(f"#{n:05d} {t}" for n, t in window)
                + f"\n\nDISPUTED PARAGRAPH: #{ordinal:05d}\nVERSIONS: "
                + "; ".join(f"«{v}»" for v in versions) + "\n")
    return ("ПЕРСОНАЖИ СЦЕНЫ:\n" + "\n".join(scene)
            + "\n\nТЕКСТ:\n" + "\n".join(f"#{n:05d} {t}" for n, t in window)
            + f"\n\nСПОРНЫЙ АБЗАЦ: #{ordinal:05d}\nВЕРСИИ: "
            + "; ".join(f"«{v}»" for v in versions) + "\n")
