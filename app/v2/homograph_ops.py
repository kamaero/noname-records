"""«Спорные ударения»: the places where the reading depends on the sentence.

The base dictionary calls 522 words of «Крыльев» ambiguous — «уже», «руки», «замок» —
and the context layer chose a reading for each of their 5 342 occurrences. Confirming
5 342 decisions by hand is not review, it is retyping, so the work is grouped the way
it actually splits:

- 400 words the model read the same way everywhere («время» 268 times out of 268):
  one decision for the word, not 268 for the places;
- 122 words it read differently in different places — «руки» 99 against 97 — where
  the sentence really does decide;
- 95 places whose reading is the rare one for their word. That is where a mistake
  hides, and it is half an hour of work rather than a week.

The rare reading is what orders everything here. A model that picks «уже́» 727 times
and «у́же» five times is telling you which five to look at; a model that splits 99
against 97 is telling you it has no idea and the fork is real.

Only `source == "context"` is under review. A mark the author placed himself is a
decision, not a proposal, and must never come back asking to be confirmed.
"""
from __future__ import annotations

import collections

from app.models import ScriptChapter
from app.v2.stress import ACUTE, default_dict_lookup
from app.v2.stress_ops import _book_segments, _effective_marks_for

CONTEXT_SOURCE = "context"


def stressed_form(word: str, vowel_offset: int) -> str:
    """«уже» + 2 → «уже́» — the word with the acute after the chosen vowel."""
    if vowel_offset is None or vowel_offset < 0 or vowel_offset >= len(word):
        return word
    return word[: vowel_offset + 1] + ACUTE + word[vowel_offset + 1 :]


def _collect(db, book_id: str, dict_lookup=None):
    """Every context-marked occurrence of an ambiguous word, with its segment."""
    lookup = dict_lookup or default_dict_lookup
    segments = _book_segments(db, book_id)
    text_of = {segment_id: text for segment_id, _chapter_id, text in segments}
    chapter_of = {segment_id: chapter_id for segment_id, chapter_id, _text in segments}
    marks = _effective_marks_for(db, list(text_of))

    ambiguous: dict[str, list[int] | None] = {}
    places: list[dict] = []
    for segment_id, rows in marks.items():
        text = text_of.get(segment_id, "")
        for start, end, vowel, source in rows:
            if source != CONTEXT_SOURCE:
                continue
            word = text[start:end].lower()
            if word not in ambiguous:
                found = lookup(word)
                ambiguous[word] = sorted(found) if isinstance(found, list) else None
            options = ambiguous[word]
            if options is None:
                continue
            places.append({
                "word": word,
                "segment_id": segment_id,
                "chapter_id": chapter_of.get(segment_id, ""),
                "text": text,
                "word_start": start,
                "word_end": end,
                "chosen": int(vowel),
                "options": options,
            })
    return places


def _chapter_index(db, book_id: str) -> dict[str, int]:
    rows = db.query(ScriptChapter.id, ScriptChapter.chapter_index).filter(ScriptChapter.book_id == book_id).all()
    return {str(chapter_id): int(index or 0) for chapter_id, index in rows}


def _readings(word: str, counts: collections.Counter) -> list[dict]:
    return [
        {"vowel_offset": vowel, "count": count, "form": stressed_form(word, vowel)}
        for vowel, count in counts.most_common()
    ]


def homograph_words(db, book_id: str, *, dict_lookup=None) -> list[dict]:
    """One row per ambiguous word: how the model read it, and how sure that looks.

    Ordered by doubt: an even split first — the model had no idea — then the words it
    read two ways at all, then the ones it never wavered on.
    """
    counts: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    options_of: dict[str, list[int]] = {}
    for place in _collect(db, book_id, dict_lookup):
        counts[place["word"]][place["chosen"]] += 1
        options_of[place["word"]] = place["options"]

    rows = []
    for word, tally in counts.items():
        total = sum(tally.values())
        readings = _readings(word, tally)
        rare = total - readings[0]["count"] if len(readings) > 1 else 0
        rows.append({
            "word": word,
            "places": total,
            "mixed": len(tally) > 1,
            "rare_places": rare,
            # 0.5 when the model split evenly, 0 when it never wavered
            "doubt": round(rare / total, 4) if total else 0.0,
            "readings": readings,
            "options": [{"vowel_offset": v, "form": stressed_form(word, v)} for v in options_of[word]],
        })
    rows.sort(key=lambda row: (-row["doubt"], -row["places"], row["word"]))
    return rows


def homograph_places(db, book_id: str, word: str, *, dict_lookup=None) -> list[dict]:
    """Every occurrence of one word, the rare reading first — that is what to check."""
    wanted = str(word or "").strip().lower()
    if not wanted:
        return []
    chapters = _chapter_index(db, book_id)
    mine = [place for place in _collect(db, book_id, dict_lookup) if place["word"] == wanted]
    if not mine:
        return []
    tally = collections.Counter(place["chosen"] for place in mine)
    common = tally.most_common(1)[0][0]
    out = []
    for place in mine:
        out.append({
            "segment_id": place["segment_id"],
            "chapter_id": place["chapter_id"],
            "chapter_index": chapters.get(place["chapter_id"], 0),
            "text": place["text"],
            "word_start": place["word_start"],
            "word_end": place["word_end"],
            "chosen": place["chosen"],
            "rare": place["chosen"] != common,
            "options": [{"vowel_offset": v, "form": stressed_form(wanted, v)} for v in place["options"]],
        })
    out.sort(key=lambda item: (not item["rare"], item["chapter_index"], item["segment_id"]))
    return out
