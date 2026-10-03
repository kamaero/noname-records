"""Operator edits to v2 stress marks: one word, settled once, applied everywhere.

In v1 a stress correction rewrote the prose of every chapter. Here the prose never
changes; a correction is a new version of the marks of each segment where the word
occurs, so the cost of «Дгарни́н, not Дга́рнин» is one row per occurrence and the
old rows stay as history. The rule follows the word into its inflected forms under
the same stem rule the Resolver uses for author words, because a name keeps its
stress through declension and the operator should not have to type every case.

Two places remember the decision so a re-run reproduces it: the book's
`pronunciation_notes` (v1 format, so the old pipeline still honours it) or, for
`scope="author"`, the author's profile shared across books. The queue is the other
half: which words still have no mark, and which homographs a model guessed at.
"""
from __future__ import annotations

import collections
import re

from sqlalchemy import func, select

from app.models import ScriptChapter
from app.pronunciation import _strip_stress, parse_pronunciation_notes
from app.services.author_profile import upsert_pronunciation
from app.services.stress_review import append_stress_term
from app.v2.models import V2Segment, V2StressMark, V2StressSkip
from app.v2.store import record_operator_intervention, store_stress_marks_bulk
from app.v2.stress import (
    ACUTE,
    GRAVE,
    _valid_offset,
    author_stem,
    default_dict_lookup,
    find_words,
    is_author_inflection,
    offset_for_form,
    offset_from_stressed_form,
)

SCOPES = ("book", "author")
# The word a mark points at: the Cyrillic run beginning at its word_start.
_RUN_AT_RE = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё́]*")
# sqlite caps bound parameters per statement; keep IN-lists comfortably below it.
_IN_CHUNK = 500


class StressTermError(ValueError):
    """A bad request, named by a short code the API turns into a 400."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _clean_word(word: str) -> str:
    return _strip_stress(str(word or "")).replace(GRAVE, "").strip().lower()


def _chapter_index_by_id(db, book_id: str) -> dict[str, int]:
    rows = db.query(ScriptChapter.id, ScriptChapter.chapter_index).filter(ScriptChapter.book_id == book_id).all()
    return {str(chapter_id): int(index or 0) for chapter_id, index in rows}


def _book_segments(db, book_id: str) -> list[tuple[str, str, str]]:
    """`(segment_id, chapter_id, text)` in reading order: chapter index, then ordinal."""
    order = _chapter_index_by_id(db, book_id)
    stmt = select(V2Segment.id, V2Segment.chapter_id, V2Segment.ordinal, V2Segment.text).where(V2Segment.book_id == book_id)
    rows = list(db.connection().execute(stmt))
    rows.sort(key=lambda row: (order.get(str(row[1]), 10**9), int(row[2] or 0)))
    return [(str(seg_id), str(chapter_id), str(text or "")) for seg_id, chapter_id, _, text in rows]


def _effective_mark_counts(db, book_id: str) -> dict[str, tuple[int, int]]:
    """segment_id → (effective version, how many marks it holds), from the covering index alone.

    A book has a few hundred thousand mark rows and fetching them is the one thing
    that cannot be made fast; counting them per (segment, version) never leaves the
    (segment_id, version) index, so the queue can tell «every word here is marked»
    for most segments without reading a single mark.
    """
    segment_ids = select(V2Segment.id).where(V2Segment.book_id == book_id)
    stmt = (
        select(V2StressMark.segment_id, V2StressMark.version, func.count())
        .where(V2StressMark.segment_id.in_(segment_ids))
        .group_by(V2StressMark.segment_id, V2StressMark.version)
    )
    out: dict[str, tuple[int, int]] = {}
    for segment_id, version, count in db.connection().execute(stmt):
        current = out.get(segment_id)
        if current is None or int(version) > current[0]:
            out[str(segment_id)] = (int(version), int(count))
    return out


def _context_marks(db, book_id: str, counts: dict[str, tuple[int, int]]) -> list[tuple[str, int]]:
    """`(segment_id, word_start)` of every effective mark the context layer wrote.

    One scan of the book's marks, reading only the rows whose source is «context»;
    the effective version is known from `counts`, so the filtering costs nothing more.
    Cheaper than fetching every segment that contains a dictionary homograph, which
    in Russian prose («руки», «воды», «замка») is most of them.
    """
    segment_ids = select(V2Segment.id).where(V2Segment.book_id == book_id)
    stmt = (
        select(V2StressMark.segment_id, V2StressMark.version, V2StressMark.word_start)
        .where(V2StressMark.source == "context", V2StressMark.segment_id.in_(segment_ids))
    )
    out: list[tuple[str, int]] = []
    for segment_id, version, start in db.connection().execute(stmt):
        if int(version) == counts.get(segment_id, (0, 0))[0]:
            out.append((str(segment_id), int(start)))
    return out


def _effective_marks_for(db, segment_ids) -> dict[str, list[tuple[int, int, int, str]]]:
    """Effective `(word_start, word_end, vowel_offset, source)` per segment, for the given ids only."""
    ids = list(dict.fromkeys(str(item) for item in segment_ids))
    out: dict[str, list[tuple[int, int, int, str]]] = {}
    for i in range(0, len(ids), _IN_CHUNK):
        chunk = ids[i : i + _IN_CHUNK]
        latest = dict(
            db.connection().execute(
                select(V2StressMark.segment_id, func.max(V2StressMark.version))
                .where(V2StressMark.segment_id.in_(chunk))
                .group_by(V2StressMark.segment_id)
            ).all()
        )
        stmt = select(
            V2StressMark.segment_id, V2StressMark.version, V2StressMark.word_start,
            V2StressMark.word_end, V2StressMark.vowel_offset, V2StressMark.source,
        ).where(V2StressMark.segment_id.in_(chunk))
        for segment_id, version, start, end, vowel, source in db.connection().execute(stmt):
            if int(version) != int(latest.get(segment_id) or 0):
                continue
            out.setdefault(str(segment_id), []).append((int(start), int(end), int(vowel), str(source or "")))
    for marks in out.values():
        marks.sort()
    return out


def _known_to_dictionaries(word: str):
    """Ответ словарей о слове (None — ни один не знает). Лениво: база весит 5.7 МБ."""
    from app.v2.stress import default_dict_lookup
    from app.v2.stress_forms import default_forms_lookup

    found = default_dict_lookup(word)
    return found if found is not None else default_forms_lookup(word)


def word_forms_in_book(db, book_id: str, word: str, *, known=None) -> list[tuple[str, int, int, str]]:
    """Every occurrence of `word` or an inflection of it: `(segment_id, start, end, surface)`.

    `known` — the dictionaries (real ones by default): «материал» is a word they know,
    so it is not taken for a form of «матери» (`is_author_inflection`).
    """
    base = _clean_word(word)
    if not base:
        return []
    known = known or _known_to_dictionaries
    stem = author_stem(base)
    out: list[tuple[str, int, int, str]] = []
    for segment_id, _chapter_id, text in _book_segments(db, book_id):
        # Cheap gate before the regex: the stem must appear somewhere, marks stripped.
        if stem not in _clean_word(text):
            continue
        for start, end in find_words(text):
            surface = text[start:end]
            if is_author_inflection(base, _clean_word(surface), known=known):
                out.append((segment_id, start, end, surface))
    return out


def _overlaps(mark: tuple, start: int, end: int) -> bool:
    return mark[0] < end and mark[1] > start


def set_place_stress(db, *, segment_id: str, word_start: int, word_end: int, vowel_offset: int,
                     source: str = "operator", actor_uid: str = "") -> dict:
    """One occurrence, one reading — the decision a homograph actually needs.

    `apply_stress_rule` marks every occurrence of a word in the book, which is right
    for a word read one way everywhere and destructive for «уже», which is «уже́» here
    and «у́же» three chapters later. This replaces the mark on exactly one span and
    carries the segment's other marks into the new version untouched. The caller commits.
    """
    segment = db.get(V2Segment, str(segment_id or "").strip())
    if segment is None:
        raise StressTermError("segment_not_found")
    start, end = int(word_start), int(word_end)
    surface = str(segment.text or "")[start:end]
    if not _valid_offset(surface, int(vowel_offset)):
        raise StressTermError("offset_not_on_vowel")

    kept = [
        {"word_start": mark[0], "word_end": mark[1], "vowel_offset": mark[2], "source": mark[3] or "dict"}
        for mark in _effective_marks_for(db, [segment.id]).get(segment.id, [])
        if not _overlaps(mark, start, end)
    ]
    replacement = {"word_start": start, "word_end": end, "vowel_offset": int(vowel_offset), "source": source}
    store_stress_marks_bulk(db, {segment.id: sorted(kept + [replacement], key=lambda m: m["word_start"])})
    return {"segment_id": segment.id, "word": surface, "vowel_offset": int(vowel_offset)}


def apply_stress_rule(db, *, book_id: str, word: str, vowel_offset: int, source: str = "operator",
                      actor_uid: str = "") -> dict:
    """Mark every occurrence of `word` (and its inflections) at `vowel_offset` of the base form.

    Each affected segment gets a new version of its marks: the effective ones for
    other words carried over unchanged, the matching words replaced. The offset is
    carried to inflected forms by `offset_for_form`; an occurrence it cannot place is
    skipped and named in `notes` rather than guessed at.
    """
    base = _clean_word(word)
    if not _valid_offset(base, vowel_offset):
        raise StressTermError("offset_not_on_vowel")
    occurrences = word_forms_in_book(db, book_id, base)
    by_segment: dict[str, list[tuple[int, int, str]]] = collections.OrderedDict()
    for segment_id, start, end, surface in occurrences:
        by_segment.setdefault(segment_id, []).append((start, end, surface))

    effective = _effective_marks_for(db, list(by_segment))
    next_marks: dict[str, list[dict]] = {}
    notes: list[str] = []
    for segment_id, found in by_segment.items():
        replaced: dict[int, dict] = {}
        for start, end, surface in found:
            offset = offset_for_form(base, int(vowel_offset), surface.lower())
            if offset is None:
                notes.append(f"{segment_id}: «{surface}» — не удалось перенести ударение на эту форму")
                continue
            replaced[start] = {"word_start": start, "word_end": end, "vowel_offset": offset, "source": source}
        if not replaced:
            continue
        spans = [(start, end) for start, end, _ in found if start in replaced]
        kept = [
            {"word_start": mark[0], "word_end": mark[1], "vowel_offset": mark[2], "source": mark[3] or "dict"}
            for mark in effective.get(segment_id, [])
            if not any(_overlaps(mark, start, end) for start, end in spans)
        ]
        next_marks[segment_id] = sorted(kept + list(replaced.values()), key=lambda m: m["word_start"])
    store_stress_marks_bulk(db, next_marks)

    return {
        "segments_updated": len(next_marks),
        "occurrences": len(occurrences),
        "skipped": len(notes),
        "notes": notes,
    }


def save_stress_term(db, *, book, word: str, stressed: str, scope: str, actor_uid: str,
                     actor_name: str = "") -> dict:
    """Remember `word=stressed` for the book or the author, and apply it to the v2 marks.

    Validation raises `StressTermError` with a code: the stressed form must be the
    word with marks added, one vowel must carry the mark, and `scope="author"` needs
    a book with an author. `scope="book"` writes the v1 notes line; `scope="author"`
    writes the profile row (and refreshes a notes line for the same word if the book
    already had one, so the two layers never disagree on a word the author settled).
    """
    base = _clean_word(word)
    form = str(stressed or "").strip()
    scope = str(scope or "book").strip().lower()
    if not base or not form:
        raise StressTermError("word_and_stressed_required")
    if scope not in SCOPES:
        raise StressTermError("bad_scope")
    if _clean_word(form) != base:
        raise StressTermError("stressed_does_not_match_word")
    offset = offset_from_stressed_form(base, form)
    if offset is None:
        raise StressTermError("no_vowel_marked")

    previous_notes = str(getattr(book, "pronunciation_notes", "") or "")
    if scope == "author":
        author_id = str(getattr(book, "author_id", "") or "")
        if not author_id:
            raise StressTermError("book_has_no_author")
        term = _strip_stress(str(word or "")).replace(GRAVE, "").strip()
        upsert_pronunciation(db, author_id, term, form, [], source="book")
        if base in parse_pronunciation_notes(previous_notes):
            book.pronunciation_notes = append_stress_term(previous_notes, base, form)
    else:
        book.pronunciation_notes = append_stress_term(previous_notes, base, form)

    applied = apply_stress_rule(db, book_id=str(book.id), word=base, vowel_offset=offset,
                                source="operator", actor_uid=actor_uid)
    result = {"word": base, "stressed": form, "scope": scope, **applied}
    record_operator_intervention(
        db, book=book, action_type="v2_stress_term", actor_uid=actor_uid, actor_name=actor_name,
        reason=f"v2 stress term «{base}» ({scope})",
        payload={
            "defect_codes": ["V2_STRESS_TERM"],
            "word": base,
            "stressed": form,
            "scope": scope,
            "previous_notes": previous_notes,
            "next_notes": str(getattr(book, "pronunciation_notes", "") or ""),
            "segments_updated": applied["segments_updated"],
            "occurrences": applied["occurrences"],
            "skipped": applied["skipped"],
        },
    )
    return result


def _ranked(counter: collections.Counter, samples: dict, limit: int) -> list[dict]:
    ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {
            "word": word,
            "count": count,
            "sample_segment_id": samples[word][0],
            "sample_chapter_index": samples[word][1],
        }
        for word, count in ordered[:limit]
    ]


def skip_stress_word(db, *, book_id: str, word: str, actor_uid: str = "") -> bool:
    """«Ударение тут не нужно» — the word leaves the queue for this book.

    Idempotent: the same word waved off twice is one row. The caller commits.
    """
    base = _clean_word(word)
    if not base:
        return False
    existing = db.query(V2StressSkip).filter(V2StressSkip.book_id == book_id, V2StressSkip.word == base).first()
    if existing is not None:
        return False
    db.add(V2StressSkip(book_id=book_id, word=base, actor_uid=str(actor_uid or "")))
    db.flush()
    return True


def unskip_stress_word(db, *, book_id: str, word: str) -> bool:
    """Put a waved-off word back in the queue. The caller commits."""
    base = _clean_word(word)
    removed = db.query(V2StressSkip).filter(V2StressSkip.book_id == book_id, V2StressSkip.word == base).delete()
    db.flush()
    return bool(removed)


def _skipped_words(db, book_id: str) -> set[str]:
    return {row[0] for row in db.query(V2StressSkip.word).filter(V2StressSkip.book_id == book_id).all()}


def stress_queue(db, book_id: str, *, limit: int = 200, offset: int = 0, scope: str = "all", dict_lookup=None) -> dict:
    """What still needs a human: unmarked words, and homographs a model settled by context.

    Marks are never loaded for the whole book. Per segment the effective version's
    mark count comes from the index (`_effective_mark_counts`); a segment whose count
    equals its word count is fully marked and is not read. Rows are fetched only for
    the segments where the counts disagree. Homographs come the other way round: the
    «context» marks are read in one scan (`_context_marks`) and only those whose word
    the base dictionary calls a homograph are reported. Marks sit on `find_words`
    spans, which is the shape both the resolver and `apply_stress_rule` write, so a
    word is matched to its mark by segment and start. A «ё» word or one carrying an
    acute in the text counts as marked without a row. Both lists are grouped by the
    lowercased word, most frequent first, and cut at `limit` each; the counts are
    occurrence totals before the cut.
    """
    lookup = dict_lookup or default_dict_lookup
    scope = str(scope or "all").strip().lower()
    skipped = _skipped_words(db, book_id)
    chapter_index = _chapter_index_by_id(db, book_id)
    counts = _effective_mark_counts(db, book_id)
    segments = _book_segments(db, book_id)
    chapter_of = {segment_id: chapter_id for segment_id, chapter_id, _ in segments}
    text_of = {segment_id: text for segment_id, _, text in segments}

    words_by_segment: dict[str, list[tuple[int, int]]] = {}
    detail: list[str] = []
    total_words = 0
    unprocessed = unprocessed_words = 0
    # A word that never appears in lower case anywhere in the book is a proper noun —
    # the author's inventions. This is the signal the base dictionary cannot give: it
    # holds words whose stress is not obvious, so «даже» is missing from it too.
    lower_anywhere: set[str] = set()
    for segment_id, _chapter_id, text in segments:
        words = find_words(text)
        for start, end in words:
            surface = text[start:end]
            if surface[:1].islower():
                lower_anywhere.add(surface.lower())
        total_words += len(words)
        marked = counts.get(segment_id, (0, 0))[1]
        # A segment no layer has touched yet is not "unresolved", it is unprocessed:
        # listing every word of an unstressed chapter would bury the real residue
        # under «это» and «его». It is counted apart and left out of the lists.
        if marked == 0 and words:
            unprocessed += 1
            unprocessed_words += len(words)
            continue
        if marked < len(words):
            words_by_segment[segment_id] = words
            detail.append(segment_id)

    marks = _effective_marks_for(db, detail)
    unresolved: collections.Counter = collections.Counter()
    samples: dict[str, tuple[str, int]] = {}
    for segment_id in detail:
        text = text_of[segment_id]
        by_start = {mark[0] for mark in marks.get(segment_id, [])}
        for start, end in words_by_segment[segment_id]:
            if start in by_start:
                continue
            surface = text[start:end]
            lower = surface.lower()
            if "ё" in lower or ACUTE in surface:
                continue
            if lower in skipped:
                continue
            unresolved[lower] += 1
            samples.setdefault(lower, (segment_id, chapter_index.get(chapter_of[segment_id], 0)))

    homographs: collections.Counter = collections.Counter()
    is_homograph: dict[str, bool] = {}
    for segment_id, start in _context_marks(db, book_id, counts):
        text = text_of.get(segment_id, "")
        match = _RUN_AT_RE.match(text, start)
        if match is None:
            continue
        lower = match.group(0).lower()
        flag = is_homograph.get(lower)
        if flag is None:
            flag = isinstance(lookup(lower), list)
            is_homograph[lower] = flag
        if flag:
            homographs[lower] += 1
            samples.setdefault(lower, (segment_id, chapter_index.get(chapter_of[segment_id], 0)))

    def is_invention(word: str) -> bool:
        return word not in lower_anywhere and lookup(word) is None

    author_words = collections.Counter({w: n for w, n in unresolved.items() if is_invention(w)})
    common_words = unresolved - author_words
    shown = unresolved
    if scope == "names":
        shown = author_words
    elif scope == "common":
        shown = common_words

    unresolved_rows = _ranked(shown, samples, offset + limit)[offset:]
    homograph_rows = _ranked(homographs, samples, offset + limit)[offset:]
    return {
        "scope": scope,
        "unresolved": unresolved_rows,
        "homographs": homograph_rows,
        "has_more": {
            "unresolved": offset + len(unresolved_rows) < len(shown),
            "homographs": offset + len(homograph_rows) < len(homographs),
        },
        "counts": {
            "unprocessed_segments": unprocessed,
            "marked": total_words - unprocessed_words - sum(unresolved.values()),
            "unresolved": sum(unresolved.values()),
            "homographs": sum(homographs.values()),
            "unresolved_words": len(unresolved),
            "homograph_words": len(homographs),
            # Additive partition of the residue, whatever list this request shows.
            "author_words": len(author_words),
            "common_words": len(common_words),
            # Legacy spelling retained for the existing «Имена и выдумки» client.
            "name_words": len(author_words),
            "skipped_words": len(skipped),
        },
    }
