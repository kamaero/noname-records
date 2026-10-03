"""Оркестрация консилиума без сети и базы: вызов модели передаётся снаружи.

Здесь живут правила, ошибка в которых ставит чужой голос или сжигает деньги: как чтец
читает главу кусками, когда глава считается прочитанной, как машина — а не модель —
выводит вердикт арбитра, кому арбитр нужен и когда прогон обязан остановиться.
"""
from __future__ import annotations

import logging

import random
import time
from dataclasses import dataclass
from typing import Callable

from app.pipeline.consilium_prompts import (
    ARBITER_SCHEMA, ARBITER_SYSTEM, ARBITER_WINDOW, NARRATOR, READER_CHUNK, READER_SCHEMA,
    READER_SYSTEM, UNSURE, arbiter_user_prompt, as_pair, reader_user_prompt,
)

logger = logging.getLogger(__name__)

Ask = Callable[[str, str, str, dict], dict]
READER_ATTEMPTS = 2
RETRY_PAUSE_SECONDS = 5
NEAR = 2
NARRATOR_NOTE = "[машинная проверка: рассказчик не доказывается цитатой — условность студии, решает человек]"


class StopRun(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def read_chapter(*, model: str, cast_lines: list[str], paragraphs: list[tuple[int, str]],
                 ask: Ask, checkpoint: Callable[[], None] = lambda: None,
                 pause: float = RETRY_PAUSE_SECONDS) -> tuple[dict[int, str], bool]:
    """Ответы чтеца по главе и признак полноты.

    Кусок, не разобранный после двух попыток, оставляет свои абзацы без ответа — и глава
    тогда НЕ прочитана: пересверка обязана её дочитать, а не пропустить как готовую.
    """
    wanted = {number for number, _ in paragraphs}
    answers: dict[int, str] = {}
    order: list[tuple[int, str]] = []
    chunk_starts = list(range(0, len(paragraphs), READER_CHUNK))
    for index, start in enumerate(chunk_starts):
        piece = paragraphs[start:start + READER_CHUNK]
        lines: list = []
        for attempt in range(1, READER_ATTEMPTS + 1):
            try:
                payload = ask(model, READER_SYSTEM, reader_user_prompt(cast_lines, piece, order),
                              READER_SCHEMA)
                lines = (payload or {}).get("lines") or []
                break
            except StopRun:
                raise
            except Exception as exc:  # noqa: BLE001 — отказ куска не валит главу
                if attempt < READER_ATTEMPTS and pause:
                    time.sleep(pause)
                elif attempt == READER_ATTEMPTS:
                    # Кусок останется непрочитанным (глава уйдёт в «неполные»); без этой
                    # строки причина — квота, сеть, отказ модели — терялась бы бесследно.
                    logger.warning("Чтец %s не прочитал кусок с абзаца %s после %s попыток: %s: %s",
                                   model, start, READER_ATTEMPTS, type(exc).__name__, str(exc)[:200])
        for line in lines:
            pair = as_pair(line)
            if pair is None or pair[0] not in wanted:
                continue
            answers[pair[0]] = pair[1]
            order.append(pair)
        if index < len(chunk_starts) - 1:  # не после последнего куска: иначе StopRun там
            checkpoint()                   # выбрасывает уже прочитанную и оплаченную главу
    return answers, wanted <= set(answers)


def _flat(text: str) -> str:
    return " ".join(str(text or "").split())


def derive_verdict(*, ordinal: int, current: str, speaker: str, evidence_kind: str,
                   evidence_para: int, evidence_quote: str, texts: dict[int, str],
                   canon: Callable[[str], str]) -> dict:
    """Вердикт выводит машина: модель лишь называет говорящего и приносит цитату."""
    quote = str(evidence_quote or "").strip()
    para = int(evidence_para if evidence_para is not None else -1)
    verbatim = bool(quote) and len(quote) <= 200 and _flat(quote) in _flat(texts.get(para, ""))
    near = abs(para - int(ordinal)) <= NEAR
    name = canon(str(speaker or "").strip())
    proven = verbatim and near and evidence_kind != "context_only" and bool(name)
    note = ""
    if not proven:
        verdict = "undecidable"
    elif name == (canon(current) or current):
        verdict = "keep_current"
    else:
        verdict = "change"
    if verdict == "change" and name == NARRATOR:
        verdict, proven, note = "undecidable", False, NARRATOR_NOTE
    return {"verdict": verdict, "speaker": name, "evidence_kind": str(evidence_kind or ""),
            "evidence_para": para, "evidence_quote": quote, "proven": proven, "note": note}


def arbitrate_place(*, chapter_index: int, ordinal: int, current: str, opus: str, sol: str,
                    texts: dict[int, str], speakers: dict[int, str], races: dict[str, str],
                    ask: Ask, canon: Callable[[str], str], model: str) -> dict:
    """Третий голос по месту. Отказ модели — место без вердикта, следующий прогон доразберёт."""
    window = [(n, texts[n]) for n in range(ordinal - ARBITER_WINDOW, ordinal + ARBITER_WINDOW + 1)
              if n in texts]
    versions: list[str] = []
    for raw in (current, opus, sol):
        name = canon(raw) or str(raw or "").strip()
        if name and name != UNSURE and name not in versions:
            versions.append(name)
    random.Random(f"{chapter_index}:{ordinal}").shuffle(versions)
    scene = {canon(speakers[n]) or speakers[n] for n, _ in window if n in speakers} | set(versions)
    scene -= {NARRATOR, UNSURE, ""}
    scene_lines = [f"- {n}" + (f" — {races[n]}" if races.get(n) else "") for n in sorted(scene)]
    no_verdict = {"verdict": "", "speaker": "", "evidence_kind": "", "evidence_para": 0,
                  "evidence_quote": "", "proven": False, "note": "", "reason": ""}
    try:
        payload = ask(model, ARBITER_SYSTEM, arbiter_user_prompt(scene_lines, window, ordinal, versions),
                      ARBITER_SCHEMA)
    except StopRun:
        raise
    except Exception as exc:  # noqa: BLE001 — отказ арбитра — «нет вердикта», а не падение прогона
        logger.warning("Арбитр %s не ответил по абзацу %s: %s: %s", model, ordinal, type(exc).__name__, str(exc)[:200])
        return dict(no_verdict)
    if not isinstance(payload, dict):  # схема не гарантирует объект — не валить прогон
        return dict(no_verdict)
    out = derive_verdict(ordinal=ordinal, current=current, speaker=payload.get("speaker"),
                         evidence_kind=str(payload.get("evidence_kind") or ""),
                         evidence_para=int(payload["evidence_para"]) if str(payload.get("evidence_para", "")).lstrip("-").isdigit() else -1,
                         evidence_quote=str(payload.get("evidence_quote") or ""),
                         texts=texts, canon=canon)
    reason = str(payload.get("reason") or "")
    out["reason"] = f"{out['note']} {reason}".strip() if out["note"] else reason
    return out


@dataclass
class ExistingState:
    status: str
    kind: str
    current: str
    opus: str
    sol: str
    has_verdict: bool


def plan_places(found: dict[tuple, tuple[str, str, str, str]],
                existing: dict[tuple, ExistingState]) -> tuple[list[tuple], list[tuple]]:
    """Кому нужен арбитр и какие находки сняты — по таблице спеки «Когда зовётся арбитр»."""
    arbitrate: list[tuple] = []
    for key, (kind, current, opus, sol) in found.items():
        row = existing.get(key)
        if row is None or row.status == "gone":
            arbitrate.append(key)
        elif row.status == "new" and (not row.has_verdict
                                      or (row.kind, row.current, row.opus, row.sol) != (kind, current, opus, sol)):
            arbitrate.append(key)
    gone = [key for key, row in existing.items() if key not in found and row.status == "new"]
    return sorted(arbitrate), sorted(gone)


class Budget:
    """Предохранитель: траты вдвое больше сметы — сбой (зацикленная модель), а не работа."""

    def __init__(self, estimate_rub: float, estimate_calls: int):
        # «Потрачено» — общий баланс RouterAI, из него же платит ASR: маленькой смете нужен запас,
        # иначе чужая сверка глав останавливает здоровый прогон.
        estimate = max(float(estimate_rub), 1.0)
        self.limit_rub = max(2 * estimate, estimate + 100)
        self.limit_calls = 2 * max(int(estimate_calls), 1)
        self.calls = 0

    def note_call(self) -> None:
        self.calls += 1

    def check(self, spent_rub: float | None) -> None:
        if spent_rub is not None:
            if spent_rub > self.limit_rub:
                raise StopRun("over_budget")
        elif self.calls > self.limit_calls:
            raise StopRun("over_budget")
