"""Снять ударения, которые правило окончаний перенесло со слова автора на чужое слово.

До 2026-09-29 форма слова автора определялась так: основа (слово без последней
гласной) плюс любые одна–три буквы. «матери=Ма́тери» давало «ма́териал», «Банке=Ба́нке» —
«ба́нкир», «ба́нкет», «ба́нкнот». Правило исправлено (`INFLECTION_ENDINGS` и
`is_author_inflection` в `app/v2/stress.py`: чужой хвост отвергается, если словарь знает
слово), но уже проставленные метки остались в базе.

Метка считается браком, только если сразу:
  * старое правило считало слово формой слова автора;
  * новое — нет;
  * ударение стоит ровно там, куда его перенесло бы старое правило.
Третье условие отсекает ручные решения оператора по одному месту (омографы): их
ставили не по правилу, и совпасть с переносом они могут только случайно.

Замена — ударение из словарей (база, затем Викисловарь), если слово читается одним
способом. Омограф или незнакомое слово — метка снимается, слово уходит в очередь
ударений как обычное.

Холостой прогон по умолчанию. Запуск из корня установки:
    .venv/bin/python3 -m scripts.v2.fix_author_stem_marks            # отчёт
    .venv/bin/python3 -m scripts.v2.fix_author_stem_marks --apply    # запись
"""
from __future__ import annotations

import argparse
import collections
import sys
from collections.abc import Callable, Mapping

from app.v2.stress import (
    ACUTE,
    _MAX_ENDING,
    _MIN_STEM,
    author_stem,
    is_author_inflection,
    offset_for_form,
)

REPAIRED_SOURCES = ("author", "operator")


def _old_rule(base: str, surface: str) -> bool:
    """Правило окончаний до исправления: основа плюс любые одна–три буквы."""
    stem = author_stem(base)
    return (len(stem) >= _MIN_STEM and surface.startswith(stem)
            and 1 <= len(surface) - len(stem) <= _MAX_ENDING)


def stem_collateral(word: str, offset: int, layer: Mapping[str, int], *, known: Callable) -> str | None:
    """Слово автора, с которого старое правило перенесло эту метку, — или None.

    `word` — в нижнем регистре и без знаков ударения; `known` — словари, как у правила.
    """
    if word in layer:
        return None
    for base, base_offset in layer.items():
        if not _old_rule(base, word) or is_author_inflection(base, word, known=known):
            continue
        if offset_for_form(base, base_offset, word) == offset:
            return base
    return None


def replacement(word: str, lookups: list[tuple[str, Callable]]) -> tuple[int, str] | None:
    """Ударение из словарей, если слово читается одним способом: (offset, source)."""
    for source, lookup in lookups:
        found = lookup(word)
        if isinstance(found, int):
            return found, source
        if found is not None:
            return None  # омограф — решать не словарю
    return None


def plan_book(db, book, *, layer: Mapping[str, int], lookups) -> tuple[dict, list[dict]]:
    """(новые метки по отрезкам, строки отчёта) для одной книги."""
    from app.v2.stress_ops import _book_segments, _effective_marks_for

    def known(word: str):
        for _source, lookup in lookups:
            found = lookup(word)
            if found is not None:
                return found
        return None

    segments = {segment_id: text for segment_id, _chapter, text in _book_segments(db, book.id)}
    effective = _effective_marks_for(db, list(segments))
    next_marks: dict[str, list[dict]] = {}
    report: list[dict] = []
    for segment_id, marks in effective.items():
        text = segments.get(segment_id, "")
        changed = False
        out: list[dict] = []
        for start, end, vowel, source in marks:
            mark = {"word_start": start, "word_end": end, "vowel_offset": vowel, "source": source or "dict"}
            word = text[start:end].replace(ACUTE, "").lower()
            base = (stem_collateral(word, vowel, layer, known=known)
                    if source in REPAIRED_SOURCES else None)
            if base is None:
                out.append(mark)
                continue
            fix = replacement(word, lookups)
            report.append({"segment_id": segment_id, "word": text[start:end], "from": base,
                           "was": vowel, "now": fix[0] if fix else None,
                           "source": fix[1] if fix else ""})
            changed = True
            if fix is not None:
                out.append({**mark, "vowel_offset": fix[0], "source": fix[1]})
        if changed and out:
            next_marks[segment_id] = out
        elif changed:
            # Пустую версию хранилище не пишет: единственная метка отрезка остаётся как
            # была, а в отчёте — отдельной строкой, чтобы её поправили руками.
            report[-1]["left_as_is"] = True
    return next_marks, report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="записать исправление (иначе только отчёт)")
    args = parser.parse_args(argv)

    from app.db import SessionLocal
    from app.models import ScriptBook
    from app.v2.store import store_stress_marks_bulk
    from app.v2.stress import default_dict_lookup, load_author_layer
    from app.v2.stress_forms import default_forms_lookup

    lookups = [("dict", default_dict_lookup), ("wiktionary", default_forms_lookup)]
    total = 0
    with SessionLocal() as db:
        for book in db.query(ScriptBook).order_by(ScriptBook.title).all():
            layer = load_author_layer(db, book)
            if not layer:
                continue
            next_marks, report = plan_book(db, book, layer=layer, lookups=lookups)
            if not report:
                continue
            total += len(report)
            print(f"\n{book.title}: {len(report)} меток, отрезков {len(next_marks)}")
            tally = collections.Counter(
                (row["word"].lower(), row["from"], row["now"], row.get("left_as_is", False)) for row in report)
            for (word, base, now, left), count in tally.most_common():
                where = "оставлена — единственная метка отрезка" if left else (
                    f"→ словарь, гласная {now}" if now is not None else "→ снята (омограф или нет в словарях)")
                print(f"  {count:4d}  {word}  (от «{base}»)  {where}")
            if args.apply:
                store_stress_marks_bulk(db, next_marks)
        if args.apply:
            db.commit()
    print(f"\nвсего: {total}" + ("" if args.apply else "  (холостой прогон; --apply для записи)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
