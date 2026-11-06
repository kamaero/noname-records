"""Характеристика роли по тексту книги — для ролей, о которых студии нечего сказать (07.10).

Диктор, позванный на пробу, «не видел характеристики»: у роли не было
ни описания в каноне, ни темперамента — только «человек» и заметка о голосе. Таких ролей в
одной книге около полусотни, в основном эпизодические, которых в энциклопедии автора нет.

Модель читает реплики роли с соседними абзацами и пишет 2–3 предложения: кто это, какой,
как говорит. Это черновик: `text_profile_by = "model"`, Читалка так и помечает, правка
руками в касте ставит «human». Только пустые поля: написанное автором, студией или прошлым
прогоном не трогаем. Каждая роль записывается сразу — оплаченное не теряется, если прогон
прервётся. Модель подменяется (`ask=`), в тестах сеть не трогается.
"""
from __future__ import annotations

from typing import Callable

from app.models import AuthorCharacter, Character
from app.services.shared_runtime import is_narrator_name
from app.v2.role_view import role_script

MODEL = "anthropic/claude-opus-5"
# Сколько текста даём на роль. Эпизодической роли хватает целиком; у длинной (их среди
# пустых почти нет — у длинных есть темперамент) берём начало — первое появление
# обычно и представляет героя.
MAX_LINES = 60
MAX_CHARS = 24000
RADIUS = 1
# Оценка до пилота — по звуковой разметке (тот же Opus, ₽ за абзац входа) плюс ответ.
RUB_PER_PARAGRAPH = 0.1
RUB_PER_CALL = 1.5

SYSTEM = """Ты помогаешь студии аудиокниг. Диктору, которого пригласили озвучить роль, нужно в двух-трёх предложениях понять, кого он играет.

Тебе дают реплики одной роли из книги с соседними абзацами. Абзацы, где говорит эта роль, отмечены «▶».

Напиши характеристику: кто это (занятие, место в истории, отношения с другими героями), какой у него нрав и как он говорит (манера, тон, лексика). Только то, что следует из текста; не выдумывай внешность, возраст или голос, если о них не сказано. Пиши по-русски, просто, без оценок вроде «интересный персонаж», без пересказа сюжета, не больше 3 предложений.

Если текст ничего не говорит о герое (одна-две безликие фразы), верни grounded=false и пустой profile."""

SCHEMA = {
    "type": "object",
    "properties": {
        "profile": {"type": "string"},
        "grounded": {"type": "boolean"},
    },
    "required": ["profile", "grounded"],
    "additionalProperties": False,
}


def candidates(db, book_id: str) -> list[Character]:
    """Роли без единого слова о том, кто они: ни описания в каноне, ни темперамента, ни черновика."""
    rows = db.query(Character).filter(Character.book_id == book_id).order_by(Character.name).all()
    canon_ids = [str(r.author_character_id) for r in rows if r.author_character_id]
    described = {str(c.id) for c in db.query(AuthorCharacter).filter(AuthorCharacter.id.in_(canon_ids)).all()
                 if str(c.description or "").strip()}
    return [r for r in rows
            if not is_narrator_name(r.name or "") and not str(r.narrator_role or "").strip()
            and not str(r.temperament or "").strip() and not str(r.text_profile or "").strip()
            and str(r.author_character_id or "") not in described]


def role_prompt(db, book_id: str, row: Character) -> tuple[str, int]:
    """Текст для модели и число абзацев в нём (для сметы). Пустая строка — у роли нет реплик."""
    script = role_script(db, book_id, row.name, radius=RADIUS, limit=MAX_LINES)
    if not script or not script["counts"]["lines"]:
        return "", 0
    head = [f"Книга: {script['book']['title']}", f"Роль: {row.name}"]
    if str(row.aliases or "").strip():
        head.append(f"Другие имена: {row.aliases}")
    if str(row.race or "").strip():
        head.append(f"Раса: {row.race}")
    parts, size, paragraphs = ["\n".join(head)], 0, 0
    for chapter in script["chapters"]:
        block = [f"\n— Глава {chapter['chapter_index']} —"]
        for seg in chapter["segments"]:
            if seg.get("after_gap"):
                block.append("…")
            block.append(("▶ " if seg["mine"] else "") + str(seg.get("text") or ""))
            paragraphs += 1
        text = "\n".join(block)
        if size and size + len(text) > MAX_CHARS:
            break
        parts.append(text)
        size += len(text)
    return "\n".join(parts), paragraphs


def estimate(db, book_id: str) -> dict:
    rows = candidates(db, book_id)
    calls = paragraphs = 0
    names = []
    for row in rows:
        prompt, count = role_prompt(db, book_id, row)
        if prompt:
            calls += 1
            paragraphs += count
            names.append(row.name)
    return {"roles": calls, "paragraphs": paragraphs, "names": names,
            "estimate_rub": round(paragraphs * RUB_PER_PARAGRAPH + calls * RUB_PER_CALL)}


def run(db, book_id: str, *, ask: Callable[[str, str, str, dict], dict],
        log: Callable[[str], None] = print, only: set[str] | None = None, limit: int = 0) -> dict:
    """Написать черновики. Коммит после каждой роли. `only` — имена ролей (пилот)."""
    done, empty, failed = [], [], []
    for row in candidates(db, book_id):
        if only and row.name not in only:
            continue
        if limit and len(done) + len(empty) + len(failed) >= limit:
            break
        prompt, _count = role_prompt(db, book_id, row)
        if not prompt:
            continue
        try:
            answer = ask(MODEL, SYSTEM, prompt, SCHEMA) or {}
        except Exception as exc:  # noqa: BLE001 — одна роль не роняет прогон, но видна в отчёте
            failed.append(row.name)
            log(f"! {row.name}: {exc}")
            continue
        profile = " ".join(str(answer.get("profile") or "").split())[:2000]
        if not answer.get("grounded") or not profile:
            empty.append(row.name)
            log(f"— {row.name}: текст о герое молчит")
            continue
        db.refresh(row)
        if str(row.text_profile or "").strip() or str(row.temperament or "").strip():
            continue  # пока модель думала, поле заполнили руками — не затираем
        row.text_profile, row.text_profile_by = profile, "model"
        db.commit()
        done.append(row.name)
        log(f"+ {row.name}: {profile}")
    return {"done": done, "empty": empty, "failed": failed}
