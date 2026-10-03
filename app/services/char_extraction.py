"""Извлечение персонажей книги моделью — шаг «Персонажи» конвейера v2.

Один запрос по всей книге, при длинной — пачками со слиянием; импорт в `characters`
и голос рассказчика (от первого лица или нет). Зовёт `app/v2/pipeline.py`. Раньше
жило в `app/script_pipeline.py`.
"""
from __future__ import annotations

import json
import re
import logging
import uuid
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session
from app.config import settings
from app.models import (
    Character,
    LlmUsageLog,
    ScriptBook,
    ScriptChapter,
    ScriptJob,
)
from app.pipeline.llm_client import _resolve_provider, call_chat
from app.services.character_names import (
    _filter_extracted_aliases,
    _is_pure_title_name,
    _is_valid_dialogue_speaker_name,
    _normalize_character_aliases,
    _normalize_character_identity_key,
)

logger = logging.getLogger(__name__)


PROMPTS_DIR = Path("app/prompts")


NARRATOR_ROLE_ALIASES = {"Рассказчик", "NARRATOR", "Narrator", "narrator"}


def _log_llm_usage_route(
    db: Session,
    book: ScriptBook,
    chapter: ScriptChapter | None,
    job: ScriptJob,
    usage: dict[str, Any] | None,
    *,
    stage: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    usage = usage or {}
    db.add(
        LlmUsageLog(
            book_id=book.id,
            chapter_id=chapter.id if chapter else "",
            job_id=job.id,
            chapter_index=chapter.chapter_index if chapter else 0,
            stage=(stage or job.stage),
            provider=(provider or job.provider),
            model=(model or job.model),
            created_by_user_id=book.created_by_user_id or "",
            created_by_name=book.created_by_name or "",
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=int(usage.get("total_tokens") or 0),
            estimated="true" if usage.get("estimated") else "false",
        )
    )


def _log_llm_usage(db: Session, book: ScriptBook, chapter: ScriptChapter | None, job: ScriptJob, usage: dict[str, Any] | None) -> None:
    _log_llm_usage_route(db, book, chapter, job, usage)


def _load_prompt_template(filename: str, fallback: str) -> str:
    prompt_path = PROMPTS_DIR / filename
    try:
        text = prompt_path.read_text(encoding="utf-8").strip()
        if text:
            return text
    except Exception as exc:
        logger.warning(
            "Не удалось прочитать шаблон промпта %s (%s) — используется встроенный fallback",
            prompt_path,
            type(exc).__name__,
        )
    return fallback


def _extract_json_block(value: str) -> str:
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise RuntimeError("LLM не вернула JSON-объект")
    return text[start:end + 1]


def _ensure_character_char_map_id(character: Character) -> str:
    current = str(getattr(character, "char_map_id", "") or "").strip()
    if current:
        return current
    current = str(character.id or "").strip() or str(uuid.uuid4())
    character.char_map_id = current
    return current


def _find_existing_character_match(
    existing_by_map_id: dict[str, Character],
    existing_by_identity_key: dict[str, Character],
    *,
    char_map_id: str,
    canonical_name: str,
    aliases: list[str],
) -> Character | None:
    if char_map_id and char_map_id in existing_by_map_id:
        return existing_by_map_id[char_map_id]
    for candidate in [canonical_name, *aliases]:
        normalized = _normalize_character_identity_key(candidate)
        if normalized and normalized in existing_by_identity_key:
            return existing_by_identity_key[normalized]
    return None


def _operator_hints_suffix(db: Session, book: ScriptBook) -> str:
    """Deterministic, escaped operator-hint block merged into the reextract prompt.

    Operator notes are guidance, not system truth — kept in their own delimited
    section so the model treats them as hints about already-present characters.
    """
    rows = (
        db.query(Character)
        .filter(Character.book_id == book.id, Character.operator_note != "")
        .order_by(Character.name.asc())
        .all()
    )
    notes = [(c.name, (getattr(c, "operator_note", None) or "").strip()) for c in rows if (getattr(c, "operator_note", None) or "").strip()]
    if not notes:
        return ""
    lines = "\n".join(f"- {name}: {note}" for name, note in notes)
    return (
        "\n\n=== Подсказки оператора (учитывай как уточнения по уже присутствующим "
        "персонажам; НЕ выдумывай новых) ===\n" + lines + "\n=== Конец подсказок ==="
    )


def _char_extraction_system_prompt(db: Session, book: ScriptBook) -> str:
    base = _load_prompt_template(
        "char_extraction_system.txt",
        (
            "Ты эксперт по анализу художественных текстов. "
            "Извлеки всех говорящих персонажей и верни строго валидный JSON."
        ),
    )
    from app.services import author_seed, canon_seed

    author_id = str(getattr(book, "author_id", "") or "")
    return (
        base
        + author_seed.author_roster_system_suffix(db, author_id)
        + canon_seed.canon_roster_system_suffix(db, book)
        + _operator_hints_suffix(db, book)
    )


def _run_char_extraction(db: Session, book: ScriptBook, job: ScriptJob) -> int:
    """Run book-level character extraction, falling back to batched merge on long books."""
    system_prompt = _char_extraction_system_prompt(db, book)

    # Build full-book text with chapter markers
    chapters = (
        db.query(ScriptChapter)
        .filter(ScriptChapter.book_id == book.id)
        .order_by(ScriptChapter.chapter_index.asc())
        .all()
    )
    batch_max_chars = int(settings.char_extraction_part_chars or 180000)
    if batch_max_chars <= 0:
        batch_max_chars = 180000
    batches = _build_char_extraction_batches(chapters, max_chars=batch_max_chars)
    merged_characters: list[dict[str, Any]] = []
    synopsis = ""
    narrative_voice: dict[str, Any] = {
        "mode": "mixed_or_unclear",
        "owner_name": "",
        "confidence": 0.0,
        "reason": "",
    }
    full_text = _render_char_extraction_batch(chapters)
    whole_book_limit = int(getattr(settings, "char_extraction_whole_book_max_chars", 350000) or 350000)
    if whole_book_limit <= 0:
        whole_book_limit = 350000

    try_whole_book = bool(chapters) and len(full_text) <= whole_book_limit
    if try_whole_book:
        try:
            whole_book_payload = _run_single_char_extraction_request(
                db=db,
                book=book,
                job=job,
                system_prompt=system_prompt,
                payload_text=full_text,
                batch_label="whole-book",
                chapter_numbers=[chapter.chapter_index for chapter in chapters],
            )
            merged_characters = whole_book_payload["characters"]
            synopsis = whole_book_payload["book_synopsis"]
            narrative_voice = _pick_stronger_narrative_voice(narrative_voice, whole_book_payload.get("narrative_voice") or {})
        except Exception as exc:
            logger.warning(
                "char_extraction whole-book pass failed, falling back to batches book=%s: %s",
                book.id,
                exc,
                exc_info=True,
            )
    if not merged_characters:
        batch_items: list[dict[str, Any]] = []
        synopsis_candidates: list[str] = []
        narrative_candidates: list[dict[str, Any]] = []
        for batch_no, batch in enumerate(batches, start=1):
            payload = _run_char_extraction_batch_with_fallback(
                db=db,
                book=book,
                job=job,
                system_prompt=system_prompt,
                chapters=batch,
                batch_label=f"batch-{batch_no}",
            )
            batch_items.extend(payload["characters"])
            if payload["book_synopsis"]:
                synopsis_candidates.append(payload["book_synopsis"])
            if isinstance(payload.get("narrative_voice"), dict):
                narrative_candidates.append(payload["narrative_voice"])
        merged_characters = _merge_extracted_characters(batch_items)
        synopsis = max(synopsis_candidates, key=len, default="")
        for candidate in narrative_candidates:
            narrative_voice = _pick_stronger_narrative_voice(narrative_voice, candidate)

    if synopsis and not str(getattr(book, "book_annotation", "") or "").strip():
        book.book_annotation = synopsis

    imported = _bulk_import_characters(db, book.id, merged_characters)
    try:
        _apply_narrative_voice_to_book(db, book, narrative_voice)
    except Exception:
        logger.warning("Failed to apply narrative_voice metadata book=%s", getattr(book, "id", ""), exc_info=True)
    return imported


def _build_char_extraction_batches(chapters: list[ScriptChapter], max_chars: int) -> list[list[ScriptChapter]]:
    batches: list[list[ScriptChapter]] = []
    current: list[ScriptChapter] = []
    current_chars = 0
    for chapter in chapters:
        chunk = len(chapter.source_text or "") + len(chapter.chapter_title or "") + 20
        if current and current_chars + chunk > max_chars:
            batches.append(current)
            current = [chapter]
            current_chars = chunk
            continue
        current.append(chapter)
        current_chars += chunk
    if current:
        batches.append(current)
    return batches or [chapters]


def _render_char_extraction_batch(chapters: list[ScriptChapter]) -> str:
    parts: list[str] = []
    for chapter in chapters:
        header = f"=== Глава {chapter.chapter_index}" + (f": {chapter.chapter_title}" if chapter.chapter_title else "") + " ==="
        parts.append(header + "\n" + (chapter.source_text or ""))
    return "\n\n".join(parts)


_CHAR_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "char_map_id": {"type": "string"},
                    "canonical_name": {"type": "string"},
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"}
                    },
                    "race": {"type": "string"},
                    "temperament": {"type": "string"},
                    "appears_in_chapters": {
                        "type": "array",
                        "items": {"type": "integer"}
                    }
                },
                "required": ["canonical_name", "aliases", "race", "temperament", "appears_in_chapters"],
                "additionalProperties": False
            }
        },
        "book_synopsis": {"type": "string"},
        "narrative_voice": {
            "type": "object",
            "properties": {
                "mode": {"type": "string"},
                "owner_name": {"type": "string"},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["mode", "owner_name", "confidence", "reason"],
            "additionalProperties": False,
        },
    },
    "required": ["characters", "book_synopsis"],
    "additionalProperties": False
}


def _run_single_char_extraction_request(
    *,
    db: Session,
    book: ScriptBook,
    job: ScriptJob,
    system_prompt: str,
    payload_text: str,
    batch_label: str,
    chapter_numbers: list[int],
) -> dict[str, Any]:
    user_prompt = (
        f"Книга: «{book.title}»\n"
        f"Режим: char_extraction\n"
        f"Сегмент: {batch_label}\n"
        f"Главы в сегменте: {', '.join(str(item) for item in chapter_numbers)}\n\n"
        f"{payload_text}\n\n"
        "Верни JSON с полями `characters` и `book_synopsis` строго по описанной схеме."
    )

    api_key, base_url, _mode = _resolve_provider(job.provider)
    raw = call_chat(
        base_url,
        api_key,
        job.model,
        system_prompt,
        user_prompt,
        mode=_mode,
        force_json=True,
        json_schema=_CHAR_EXTRACTION_SCHEMA,
    )
    _log_llm_usage(db, book, None, job, raw.get("usage") or {})
    return _parse_char_extraction_payload(raw.get("content", ""))


def _run_char_extraction_batch_with_fallback(
    *,
    db: Session,
    book: ScriptBook,
    job: ScriptJob,
    system_prompt: str,
    chapters: list[ScriptChapter],
    batch_label: str,
    ) -> dict[str, Any]:
    chapter_numbers = [int(chapter.chapter_index or 0) for chapter in chapters if int(chapter.chapter_index or 0) > 0]
    try:
        return _run_single_char_extraction_request(
            db=db,
            book=book,
            job=job,
            system_prompt=system_prompt,
            payload_text=_render_char_extraction_batch(chapters),
            batch_label=batch_label,
            chapter_numbers=chapter_numbers,
        )
    except Exception:
        if len(chapters) <= 1:
            raise
        midpoint = max(1, len(chapters) // 2)
        left = chapters[:midpoint]
        right = chapters[midpoint:]
        logger.warning(
            "char_extraction batch parse failed, splitting batch book=%s label=%s chapters=%s -> %s/%s",
            book.id,
            batch_label,
            chapter_numbers,
            [int(ch.chapter_index or 0) for ch in left],
            [int(ch.chapter_index or 0) for ch in right],
        )
        items: list[dict[str, Any]] = []
        synopsis_candidates: list[str] = []
        narrative_voice = {"mode": "mixed_or_unclear", "owner_name": "", "confidence": 0.0, "reason": ""}
        if left:
            left_payload = _run_char_extraction_batch_with_fallback(
                db=db,
                book=book,
                job=job,
                system_prompt=system_prompt,
                chapters=left,
                batch_label=f"{batch_label}.a",
            )
            items.extend(left_payload["characters"])
            if left_payload["book_synopsis"]:
                synopsis_candidates.append(left_payload["book_synopsis"])
            narrative_voice = _pick_stronger_narrative_voice(narrative_voice, left_payload.get("narrative_voice") or {})
        if right:
            right_payload = _run_char_extraction_batch_with_fallback(
                db=db,
                book=book,
                job=job,
                system_prompt=system_prompt,
                chapters=right,
                batch_label=f"{batch_label}.b",
            )
            items.extend(right_payload["characters"])
            if right_payload["book_synopsis"]:
                synopsis_candidates.append(right_payload["book_synopsis"])
            narrative_voice = _pick_stronger_narrative_voice(narrative_voice, right_payload.get("narrative_voice") or {})
        return {
            "characters": items,
            "book_synopsis": max(synopsis_candidates, key=len, default=""),
            "narrative_voice": narrative_voice,
        }


def _parse_char_extraction_payload(content: str) -> dict[str, Any]:
    try:
        clean = content.strip()
        if clean.startswith("```"):
            clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        data = json.loads(_extract_json_block(clean))
    except Exception as exc:
        raise RuntimeError(f"char_extraction: не удалось распарсить JSON: {exc}\nContent: {content[:300]}") from exc

    characters = data.get("characters") or []
    if not characters:
        raise RuntimeError("char_extraction: JSON не содержит поля 'characters' или список пуст")
    synopsis = str(data.get("book_synopsis") or "").strip()
    raw_voice = data.get("narrative_voice") or {}
    if not isinstance(raw_voice, dict):
        raw_voice = {}
    mode = str(raw_voice.get("mode") or "mixed_or_unclear").strip().lower()
    if mode not in {"neutral_narrator", "character_pov", "mixed_or_unclear"}:
        mode = "mixed_or_unclear"
    owner_name = str(raw_voice.get("owner_name") or "").strip()
    try:
        confidence = float(raw_voice.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(raw_voice.get("reason") or "").strip()[:255]
    return {
        "characters": [item for item in characters if isinstance(item, dict)],
        "book_synopsis": synopsis,
        "narrative_voice": {
            "mode": mode,
            "owner_name": owner_name,
            "confidence": confidence,
            "reason": reason,
        },
    }


def _pick_stronger_narrative_voice(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    current = current if isinstance(current, dict) else {}
    candidate = candidate if isinstance(candidate, dict) else {}
    current_mode = str(current.get("mode") or "mixed_or_unclear").strip().lower()
    candidate_mode = str(candidate.get("mode") or "mixed_or_unclear").strip().lower()
    current_conf = float(current.get("confidence") or 0.0)
    candidate_conf = float(candidate.get("confidence") or 0.0)

    if candidate_mode == "character_pov":
        if current_mode != "character_pov" or candidate_conf >= current_conf:
            return {
                "mode": "character_pov",
                "owner_name": str(candidate.get("owner_name") or "").strip(),
                "confidence": max(0.0, min(1.0, candidate_conf)),
                "reason": str(candidate.get("reason") or "").strip()[:255],
            }
        return current
    if current_mode == "character_pov":
        return current
    if candidate_mode == "neutral_narrator" and (current_mode != "neutral_narrator" or candidate_conf >= current_conf):
        return {
            "mode": "neutral_narrator",
            "owner_name": "",
            "confidence": max(0.0, min(1.0, candidate_conf)),
            "reason": str(candidate.get("reason") or "").strip()[:255],
        }
    return current


def _apply_narrative_voice_to_book(db: Session, book: ScriptBook, narrative_voice: dict[str, Any]) -> None:
    mode = str((narrative_voice or {}).get("mode") or "mixed_or_unclear").strip().lower()
    raw_owner_name = str((narrative_voice or {}).get("owner_name") or "").strip()
    reason = str((narrative_voice or {}).get("reason") or "").strip()[:255]
    try:
        confidence = float((narrative_voice or {}).get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    book.narrative_owner_name = ""
    book.narrative_owner_confidence = confidence
    book.narrative_owner_reason = reason

    chars = db.query(Character).filter(Character.book_id == book.id).all()
    normalized_owner = _normalize_character_identity_key(raw_owner_name)
    owner_character: Character | None = None
    if mode == "character_pov" and normalized_owner:
        for character in chars:
            names = [character.name, *_normalize_character_aliases(character.aliases)]
            keys = {_normalize_character_identity_key(item) for item in names}
            if normalized_owner in keys:
                owner_character = character
                break

    for character in chars:
        narrator_role = str(getattr(character, "narrator_role", "") or "").strip()
        if owner_character and character.id == owner_character.id:
            character.narrator_role = "Рассказчик"
            book.narrative_owner_name = character.name
        elif narrator_role in NARRATOR_ROLE_ALIASES:
            character.narrator_role = ""

    if not owner_character and raw_owner_name and mode == "character_pov":
        book.narrative_owner_name = raw_owner_name
        book.narrative_owner_reason = (reason or "POV owner detected by char_extraction but not matched in character index.")[:255]


def _merge_extracted_characters(characters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for item in characters:
        canonical_name = str(item.get("canonical_name") or "").strip()
        if not _is_valid_dialogue_speaker_name(canonical_name):
            continue
        aliases = _filter_extracted_aliases(
            canonical_name,
            [str(alias).strip() for alias in (item.get("aliases") or []) if str(alias).strip()],
        )
        cn_key = _normalize_character_identity_key(canonical_name)
        cn_is_title = _is_pure_title_name(canonical_name)
        normalized_keys = {
            cn_key,
            *[_normalize_character_identity_key(alias) for alias in aliases],
        }
        normalized_keys.discard("")
        target: dict[str, Any] | None = None
        for existing in merged:
            existing_canonical = str(existing.get("canonical_name") or "")
            existing_aliases = [str(alias).strip() for alias in (existing.get("aliases") or []) if str(alias).strip()]
            ex_cn_key = _normalize_character_identity_key(existing_canonical)
            existing_keys = {
                ex_cn_key,
                *[_normalize_character_identity_key(alias) for alias in existing_aliases],
            }
            existing_keys.discard("")
            # Two extracted records are the same character only when a *canonical
            # name* of one appears among the other's name/aliases. A merely-shared
            # alias (especially a generic title like «барон»/«Тёмный Господин»)
            # is NOT enough — that bridged distinct demonlords into one mega-merge.
            # A canonical that is itself a pure title also cannot bridge.
            canon_match = (
                (cn_key and not cn_is_title and cn_key in existing_keys)
                or (ex_cn_key and not _is_pure_title_name(existing_canonical) and ex_cn_key in normalized_keys)
            )
            if canon_match:
                target = existing
                break
        if not target:
            target = {
                "char_map_id": str(item.get("char_map_id") or "").strip() or str(uuid.uuid4()),
                "canonical_name": canonical_name,
                "aliases": [],
                "race": "",
                "temperament": "",
                "appears_in_chapters": [],
            }
            merged.append(target)
        target_aliases = {str(alias).strip() for alias in (target.get("aliases") or []) if str(alias).strip()}
        target_aliases.update(aliases)
        target_aliases.discard(target.get("canonical_name") or "")
        target["aliases"] = sorted(target_aliases)
        race = str(item.get("race") or "").strip()
        temperament = str(item.get("temperament") or "").strip()
        if race and len(race) > len(str(target.get("race") or "")):
            target["race"] = race
        if temperament and len(temperament) > len(str(target.get("temperament") or "")):
            target["temperament"] = temperament
        existing_chapters = {int(ch) for ch in (target.get("appears_in_chapters") or []) if str(ch).isdigit() or isinstance(ch, int)}
        for ch in (item.get("appears_in_chapters") or []):
            if str(ch).isdigit() or isinstance(ch, int):
                existing_chapters.add(int(ch))
        target["appears_in_chapters"] = sorted(existing_chapters)
    return merged


def _bulk_import_characters(db: Session, book_id: str, characters: list[dict[str, Any]]) -> int:
    """Upsert extracted characters into Character table. Returns imported count."""
    existing_rows = db.query(Character).filter(Character.book_id == book_id).all()
    existing_by_map_id: dict[str, Character] = {}
    existing_by_identity_key: dict[str, Character] = {}
    for char in existing_rows:
        char_map_id = _ensure_character_char_map_id(char)
        existing_by_map_id[char_map_id] = char
        for candidate in [char.name, *_normalize_character_aliases(char.aliases)]:
            normalized = _normalize_character_identity_key(candidate)
            if normalized:
                existing_by_identity_key[normalized] = char

    imported = 0
    for entry in _merge_extracted_characters(characters):
        name = str(entry.get("canonical_name") or "").strip()
        if not name:
            continue
        aliases_list = _filter_extracted_aliases(
            name,
            [a.strip() for a in (entry.get("aliases") or []) if str(a).strip()],
        )
        aliases_str = ",".join(aliases_list) if aliases_list else name
        race = str(entry.get("race") or "").strip()
        temperament = str(entry.get("temperament") or "").strip()
        appears_raw = entry.get("appears_in_chapters") or []
        appears_str = ",".join(str(int(x)) for x in appears_raw if str(x).isdigit() or isinstance(x, int))
        char_map_id = str(entry.get("char_map_id") or "").strip() or str(uuid.uuid4())

        char = _find_existing_character_match(
            existing_by_map_id,
            existing_by_identity_key,
            char_map_id=char_map_id,
            canonical_name=name,
            aliases=aliases_list,
        )
        if char:
            _ensure_character_char_map_id(char)
            if not (char.aliases or "").strip():
                char.aliases = aliases_str
            elif aliases_list:
                merged_aliases = set(_normalize_character_aliases(char.aliases))
                merged_aliases.update(aliases_list)
                merged_aliases.discard(char.name)
                char.aliases = ",".join(sorted(merged_aliases))
            if not (char.race or "").strip() and race:
                char.race = race
            if not (char.temperament or "").strip() and temperament:
                char.temperament = temperament
            if appears_str:
                old_set = {x.strip() for x in (char.appears_in or "").split(",") if x.strip()}
                new_set = {x for x in appears_str.split(",") if x}
                merged = sorted(old_set | new_set, key=lambda x: int(x) if x.isdigit() else 10**9)
                char.appears_in = ",".join(merged)
            if not (char.char_map_id or "").strip():
                char.char_map_id = char_map_id
            continue

        char = Character(
            book_id=book_id,
            char_map_id=char_map_id,
            name=name,
            aliases=aliases_str,
            race=race,
            temperament=temperament,
            appears_in=appears_str,
        )
        db.add(char)
        db.flush()
        imported += 1
        existing_by_map_id[char.char_map_id] = char
        for candidate in [char.name, *_normalize_character_aliases(char.aliases)]:
            normalized = _normalize_character_identity_key(candidate)
            if normalized:
                existing_by_identity_key[normalized] = char

    db.flush()
    return imported
