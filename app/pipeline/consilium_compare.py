"""Сведение ответов двух слепых чтецов с нынешней разметкой.

Чистая функция: ни базы, ни сети. Чтецы читали книгу, не видя ни разметки, ни отметок
автора, ни ответа друг друга, — здесь их ответы впервые встречаются.

Роды находок разной природы, и мерить их одной мерой нельзя (калибровка 2026-09-12):
«чужой голос» — факт текста, а «граница рассказчика» — условность студии (песни, надписи,
хоры, воспоминание героя о себе получают собственный голос). Поэтому они не смешиваются в
один список, а расходятся по родам сразу здесь.
"""
from __future__ import annotations

from dataclasses import dataclass

NARRATOR = "Рассказчик"
UNSURE = "UNSURE"


@dataclass
class Finding:
    chapter_index: int
    ordinal: int
    kind: str
    current: str
    readers: str
    opus: str
    sol: str


def compare(answers_opus: dict, answers_sol: dict, current: dict,
            narration_names: dict, stats: dict | None = None) -> list[Finding]:
    """Находки по всем абзацам, где ответили ОБА чтеца.

    Абзац, который хоть один чтец пропустил, не рассматривается вовсе: молчание — дыра в
    прогоне, а не голос за нынешнюю разметку. Считать его согласием значило бы прятать
    неполноту прогона в успокоительное число.

    **Правило большинства, когда чтецы разошлись между собой.** Ответов три: сценарий и
    два чтеца. Если один из чтецов сказал то же, что стоит в сценарии, — это два голоса
    из трёх, место закрыто, и одинокое мнение второго находкой не становится. `UNSURE`
    одного считается тем же: он не голосует против сценария, и спор второго остаётся
    один на один с утверждённой разметкой. Находкой «чтецы врозь» остаётся только случай,
    где оба назвали имена, имена разные и НИ ОДНО не совпало со сценарием: место
    объективно трудное, машине его не решить.

    Правило защитимо, но дорого: на настоящем прогоне оно снимает 377 мест из 387, то
    есть почти весь сигнал о разногласии чтецов. Поэтому выемки СЧИТАЮТСЯ — передайте
    словарь `stats`, и он заполнится числами, которые скрипт печатает владельцу. Правило,
    которое режет 97 % сигнала, обязано быть видимым, иначе его нельзя ни обсудить, ни
    пересмотреть.
    """
    counters = stats if stats is not None else {}
    for name in ("dropped_one_agrees_with_script", "dropped_unsure_from_one",
                 "dropped_unsure_other_disputes_script"):
        counters.setdefault(name, 0)

    out: list[Finding] = []
    for key in sorted(set(answers_opus) & set(answers_sol) & set(current)):
        chapter_index, ordinal = key
        stands, a, b = current[key], answers_opus[key], answers_sol[key]
        if a != b:
            # Оба ответили одинаково неверно — согласие чтецов; оба по-разному и оба не
            # как в сценарии — объективно трудное место.
            if UNSURE not in (a, b) and stands not in (a, b):
                out.append(Finding(chapter_index, ordinal, "readers_split", stands, "", a, b))
            elif UNSURE in (a, b):
                counters["dropped_unsure_from_one"] += 1
                other = b if a == UNSURE else a
                if other != UNSURE and other != stands:
                    counters["dropped_unsure_other_disputes_script"] += 1
            else:
                counters["dropped_one_agrees_with_script"] += 1
            continue
        if a == stands or a == UNSURE:
            continue
        if stands != NARRATOR and a != NARRATOR:
            named = str(narration_names.get(key) or "").strip()
            kind = "identity_play" if named and named != stands else "wrong_voice"
        else:
            kind = "narrator_border"
        out.append(Finding(chapter_index, ordinal, kind, stands, a, a, b))
    return out
