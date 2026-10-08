"""Stress marks as an overlay: positions over text that never changes.

The old pipeline writes U+0301 into the prose, so correcting one stress mark means
regenerating a chapter. Here a mark is (word span, vowel offset, source) and the
prose stays as extracted; the mark can be rendered, hidden or overridden by a newer
row without anyone touching the text.

The sources form a chain with a fixed order, and the order is the whole point:

    text     — a mark already present in the source (an operator wrote it there)
    author   — the author's own list; nobody else knows how «Бимькмолепус» sounds
    rule     — «ё» is always stressed, no lookup needed
    dict     — the pronunciation term dictionary, then the base dictionary, but only
               forms with a single reading; a homograph is deferred to context
    wiktionary — every inflected form Wiktionary lists (`stress_forms`): the base
               dictionary has «толи́ка», this has «толи́ку»; asked only when both
               dictionaries are silent, a homograph again deferred to context
    context  — a model that reads the whole segment (RUAccent), for homographs and
               words no dictionary has
    residue  — an optional last resort (an LLM); a hook only, not wired yet

Each layer only sees what the previous ones left, and the report says which layer
answered for how many words, because that is where a human looks first when a
reader complains about a mark.

Offsets: `vowel_offset` is the character index of the stressed vowel inside the word
(`word_start + vowel_offset` is its index in the text). A word already carrying a
combining acute keeps it inside its span, so offsets on such a word count the mark
as a character — which is why v2 keeps the text clean in the first place.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

ACUTE = "́"
GRAVE = "̀"
VOWELS = "аеёиоуыэюя"

# A run of Cyrillic letters, with any combining acute that a marked source carries.
_RUN_RE = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё́]*")
# The neighbours of a word across a plain hyphen: «кто-» before «нибудь», «-нибудь» after «кто».
_LEFT_PART_RE = re.compile(r"[А-Яа-яЁё́]+-$")
_RIGHT_PART_RE = re.compile(r"-[А-Яа-яЁё][А-Яа-яЁё́]*")
# A run with at least two vowels, in one pass: the lookahead cannot cross the run's
# boundary (its classes are Cyrillic only), and a run with two vowels is caught at
# its first letter, so no suffix of a run is ever taken for a word. This is
# `vowel_count(run) >= 2` without the Python loop, which was the cost of a book-wide
# tool calling `find_words` on 13k segments.
_V = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
_WORD_RE = re.compile(rf"(?=[А-Яа-яЁё́]*[{_V}][А-Яа-яЁё́]*[{_V}])[А-Яа-яЁё][А-Яа-яЁё́]*")


@dataclass(frozen=True)
class StressMark:
    word_start: int
    word_end: int
    vowel_offset: int
    source: str
    confidence: float = 1.0


def is_vowel(ch: str) -> bool:
    return ch.lower() in VOWELS


def vowel_count(word: str) -> int:
    return sum(1 for ch in word if is_vowel(ch))


def cyrillic_runs(text: str) -> list[tuple[int, int]]:
    """Every Cyrillic word in `text`, one-vowel words included — the unit other tools tokenise on."""
    return [(m.start(), m.end()) for m in _RUN_RE.finditer(text or "")]


def find_words(text: str) -> list[tuple[int, int]]:
    """Spans of the words that need a mark: Cyrillic, at least two vowels.

    A one-vowel word has nowhere else to put the stress; a hyphenated compound is
    split into its parts so that every part is a word a tokeniser agrees on.
    """
    return [(m.start(), m.end()) for m in _WORD_RE.finditer(text or "")]


def offset_from_stressed_form(word: str, form: str) -> int | None:
    """Character offset of the stressed vowel in `word`, read off a dictionary `form`.

    The form carries U+0301 after the vowel (`замо́к`), possibly a secondary U+0300
    somewhere else, or no acute at all when the stress sits on «ё». When the form is
    the same length as the word the offset carries over directly; otherwise (a «ё»
    the text spells «е», a hyphen the text does not have) the vowel's ordinal is used.
    """
    stripped: list[str] = []
    acute_at = -1
    for ch in form or "":
        if ch == ACUTE:
            if acute_at < 0 and stripped:
                acute_at = len(stripped) - 1
            continue
        if ch == GRAVE:
            continue
        stripped.append(ch)
    plain = "".join(stripped)
    if acute_at < 0:
        acute_at = plain.find("ё")
        if acute_at < 0:
            return None
    if not is_vowel(plain[acute_at]):
        return None
    word_plain = word.replace(ACUTE, "").replace(GRAVE, "")
    if len(plain) == len(word_plain) and is_vowel(word_plain[acute_at]):
        return acute_at
    ordinal = vowel_count(plain[: acute_at + 1]) - 1
    return _offset_of_vowel_ordinal(word_plain, ordinal)


def _offset_of_vowel_ordinal(word: str, ordinal: int) -> int | None:
    seen = -1
    for i, ch in enumerate(word):
        if is_vowel(ch):
            seen += 1
            if seen == ordinal:
                return i
    return None


def _offset_of_text_mark(word: str) -> int | None:
    pos = word.find(ACUTE)
    if pos <= 0 or not is_vowel(word[pos - 1]):
        return None
    return pos - 1


def _valid_offset(word: str, offset) -> bool:
    return isinstance(offset, int) and 0 <= offset < len(word) and is_vowel(word[offset])


def render_marks(text: str, marks: Iterable[StressMark]) -> str:
    """The text with U+0301 written after each marked vowel — for display and for the eval."""
    out = text
    for mark in sorted(marks, key=lambda m: m.word_start + m.vowel_offset, reverse=True):
        pos = mark.word_start + mark.vowel_offset + 1  # right after the vowel
        if 0 < pos <= len(out) and out[pos : pos + 1] != ACUTE:
            out = out[:pos] + ACUTE + out[pos:]
    return out


def _compound_span(text: str, start: int, end: int) -> tuple[int, int] | None:
    """Extend a word across plain hyphens to the whole compound it belongs to, if any."""
    s, e = start, end
    while True:
        m = _LEFT_PART_RE.search(text, 0, s)
        if m is None:
            break
        s = m.start()
    while True:
        m = _RIGHT_PART_RE.match(text, e)
        if m is None:
            break
        e = m.end()
    if (s, e) == (start, end):
        return None
    return s, e


DictLookup = Callable[[str], "int | list[int] | None"]
ContextLayer = Callable[[str], "Mapping[int, int] | None"]
ResidueLayer = Callable[[list[tuple[int, str]]], "Mapping[int, int] | None"]


class Resolver:
    """The chain of sources. Each layer is a plain callable so tests can fake any of them.

    `author` maps a lowercased word to its stressed vowel offset. `dict_lookup` takes a
    lowercased word and returns an offset, a list of offsets (homograph) or None;
    `forms_lookup` answers the same way from the Wiktionary word forms.
    `context` takes the whole segment and returns {word_index: offset} over
    `find_words`. `residue` takes [(word_index, word)] and answers the same way.
    """

    def __init__(
        self,
        *,
        author: Mapping[str, int] | None = None,
        dict_lookup: DictLookup | None = None,
        forms_lookup: DictLookup | None = None,
        context: ContextLayer | None = None,
        residue: ResidueLayer | None = None,
    ) -> None:
        self.author = dict(author or {})
        self.author_stems = author_stems(self.author)
        self.dict_lookup = dict_lookup or (lambda word: None)
        self.forms_lookup = forms_lookup or (lambda word: None)
        self.context = context
        self.residue = residue

    def author_offset(self, lower: str) -> tuple[int, float] | None:
        """(offset, confidence) from the author's list — exact form, or an inflected one.

        The list holds base forms («Полумрак») and the prose inflects them
        («Полумрака», «Погтду»). A name keeps its stress through inflection, so a word
        that is an author word, or its stem plus a short ending, gets the same offset;
        the stem match is reported with lower confidence because it is a guess about
        morphology, not something the author wrote.
        """
        offset = self.author.get(lower)
        if _valid_offset(lower, offset):
            return offset, 1.0
        for k in range(len(lower) - 1, _MIN_STEM - 1, -1):
            if len(lower) - k > _MAX_ENDING:
                break
            if lower[k:] not in INFLECTION_ENDINGS and self.knows(lower):
                continue
            offset = self.author_stems.get(lower[:k])
            if offset is not None and _valid_offset(lower, offset):
                return offset, 0.9
        return None

    def knows(self, lower: str) -> bool:
        """Знает ли слово хоть один словарь — одним ударением или как омограф."""
        return self.dict_lookup(lower) is not None or self.forms_lookup(lower) is not None

    @classmethod
    def from_author_rows(cls, rows, **kwargs) -> "Resolver":
        """Build the author layer from `AuthorPronunciation`-shaped rows (term, stressed)."""
        return cls(author=author_map_from_rows(rows), **kwargs)

    @classmethod
    def default(cls, *, author: Mapping[str, int] | None = None, context=None, residue=None) -> "Resolver":
        """Wire the real dictionaries. Imported lazily: the base dictionary is 5.7 MB."""
        from app.v2.stress_forms import default_forms_lookup

        return cls(author=author, dict_lookup=default_dict_lookup, forms_lookup=default_forms_lookup,
                   context=context, residue=residue)


_MIN_STEM = 4
_MAX_ENDING = 3
# Окончания, которыми склоняется слово автора. Основа плюс любые три буквы тянула
# ударение на другое слово с тем же началом — «матери» давало «ма́териал», «Банке» —
# «ба́нкир» и «ба́нкнот». Но та же вольность верно несёт ударение на производные
# выдуманных слов: «хилакто́кка», «полумра́кцы», «фамиллиа́рный». Поэтому хвост не из
# этого списка отвергается, только если обычный словарь знает слово: тогда это
# своё слово, а не форма авторского. Игуме́нья у автора остаётся за ним — окончание.
INFLECTION_ENDINGS = frozenset({
    "а", "я", "о", "е", "ё", "и", "ы", "у", "ю", "ь", "й",
    "ой", "ей", "ёй", "ою", "ею", "ом", "ем", "ём", "ам", "ям", "ах", "ях", "ов", "ев", "ёв",
    "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ым", "им", "ых", "их",
    "ия", "ии", "ию", "ие", "ье", "ья", "ьи", "ью", "ьё", "ей",
    "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ием", "иям", "иях", "ией",
    "ьям", "ьях", "ьев", "ьей", "ьми", "ьём", "ьем", "ьею",
})

# Where `scripts/v2/import_author_stress.py` drops the author's imported lists.
def author_stress_dir() -> Path:
    from app.paths import data_path
    return data_path("author_stress")


def author_stem(word: str) -> str:
    """The part of an author word an ending replaces: everything but a final vowel."""
    return word[:-1] if word and is_vowel(word[-1]) else word


def author_stems(author: Mapping[str, int]) -> dict[str, int]:
    """Stem → offset for inflection: a final vowel is the part an ending replaces."""
    stems: dict[str, int] = {}
    for word, offset in author.items():
        stem = author_stem(word)
        if len(stem) >= _MIN_STEM and offset < len(stem):
            stems.setdefault(stem, offset)
    return stems


def is_author_inflection(base: str, surface: str, *, known: DictLookup | None = None) -> bool:
    """Whether `surface` is `base` or an inflected form of it under the Resolver's rule.

    Both lowercase. The rule is the one `Resolver.author_offset` applies: the stem
    (base minus a final vowel) is at least `_MIN_STEM` letters and the surface adds
    an ending of one to `_MAX_ENDING` letters to it; an ending outside
    `INFLECTION_ENDINGS` counts only while no dictionary (`known`) has the surface as a
    word of its own. Kept here so an operator tool
    that asks «where else does this word occur?» agrees with the layer that marks it.
    """
    if not base or not surface:
        return False
    if surface == base:
        return True
    stem = author_stem(base)
    if len(stem) < _MIN_STEM or not surface.startswith(stem):
        return False
    ending = surface[len(stem):]
    if not 1 <= len(ending) <= _MAX_ENDING:
        return False
    return ending in INFLECTION_ENDINGS or known is None or known(surface) is None


def offset_for_form(base: str, base_offset: int, surface: str) -> int | None:
    """Carry a stressed-vowel offset from `base` over to an inflected `surface` form.

    The offset transfers directly when it still lands on a vowel of the surface — the
    stem is shared, so it usually does. When the ending replaced that vowel («адорази́»
    → «адоразя́ми») the vowel's ordinal is used instead; None when even that fails.
    """
    if _valid_offset(surface, base_offset):
        return base_offset
    if not _valid_offset(base, base_offset):
        return None
    ordinal = vowel_count(base[: base_offset + 1]) - 1
    return _offset_of_vowel_ordinal(surface, ordinal)


def author_map_from_rows(rows) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        term = (getattr(row, "term", "") or "").replace(ACUTE, "").strip().lower()
        stressed = (getattr(row, "stressed", "") or "").strip()
        if not term or not stressed:
            continue
        offset = offset_from_stressed_form(term, stressed)
        if offset is not None:
            out[term] = offset
    return out


def author_map_from_notes(notes: str) -> dict[str, int]:
    """From a book's `pronunciation_notes`: one `слово=сло́во` per line, as v1 writes them."""
    from app.pronunciation import parse_pronunciation_notes

    out: dict[str, int] = {}
    for term, stressed in parse_pronunciation_notes(notes or "").items():
        offset = offset_from_stressed_form(term, stressed)
        if offset is not None:
            out[term] = offset
    return out


def author_map_from_json_dir(directory, *, author_id: str = "") -> dict[str, int]:
    """Every `data/author_stress/*.json` list, skipping files stamped for another author."""
    import json
    from pathlib import Path

    out: dict[str, int] = {}
    folder = Path(directory)
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stamped = str(data.get("author_id") or "").strip()
        if stamped and author_id and stamped != author_id:
            continue
        out.update(author_map_from_entries(data.get("entries") or []))
    return out


def load_author_layer(db, book, *, json_dir=None) -> dict[str, int]:
    """The author layer for a book, from every place the author's stresses live.

    Priority, high to low: the book's own `pronunciation_notes` (what was settled in
    this book's editor), the `author_pronunciations` rows of the book's author (the
    profile shared across books), and the imported lists in `data/author_stress/`.
    A word present in several wins from the higher one — the book's editor is the
    most recent human decision, the json import the oldest.
    """
    from app.models import AuthorPronunciation

    author_id = str(getattr(book, "author_id", "") or "")
    layer = author_map_from_json_dir(author_stress_dir() if json_dir is None else json_dir, author_id=author_id)
    if author_id:
        rows = db.query(AuthorPronunciation).filter(AuthorPronunciation.author_id == author_id).all()
        layer.update(author_map_from_rows(rows))
    layer.update(author_map_from_notes(getattr(book, "pronunciation_notes", "") or ""))
    return layer


def author_map_from_entries(entries) -> dict[str, int]:
    """From `AuthorStress` entries (or the dicts in `data/author_stress/*.json`)."""
    out: dict[str, int] = {}
    for entry in entries:
        if isinstance(entry, Mapping):
            word, offset = entry.get("word"), entry.get("vowel_index")
        else:
            word, offset = getattr(entry, "word_lower", None), getattr(entry, "vowel_index", None)
        if word and _valid_offset(word, offset):
            out[str(word).lower()] = int(offset)
    return out


def default_dict_lookup(word: str):
    """Term dictionary first, then the base dictionary — the order the spec fixes.

    With one exception: when either dictionary calls the word a homograph, it is one.
    The term dictionary lists «две́ри» as a single form; the base dictionary knows
    «на двери́» exists. Taking the first answer would silently hide the ambiguity
    from the context layer, which is the only one that can settle it.
    """
    from app.pronunciation import load_pronunciation_dict
    from app.v2 import stress_dict as base_dict

    term = load_pronunciation_dict().get(word)
    base = base_dict.lookup(word)
    if isinstance(term, list) or isinstance(base, list):
        forms = [*(term if isinstance(term, list) else [term] if term else []),
                 *(base if isinstance(base, list) else [base] if base else [])]
        options = sorted({o for o in (offset_from_stressed_form(word, f) for f in forms) if o is not None})
        if len(options) == 1:
            return options[0]
        return options or None
    for form in (term, base):
        if isinstance(form, str):
            offset = offset_from_stressed_form(word, form)
            if offset is not None:
                return offset
    return None


def _lookup_with_compound(text: str, start: int, end: int, lookup: DictLookup):
    """Look the part up; when unknown, look the whole hyphenated compound up instead.

    Returns (found, resolved_without_mark): the compound may put its stress in another
    part, in which case this part is answered — by silence — and must not be sent on.
    """
    word = text[start:end].lower()
    found = lookup(word)
    if found is not None:
        return found, False
    span = _compound_span(text, start, end)
    if span is None:
        return None, False
    compound = text[span[0] : span[1]].lower()
    found = lookup(compound)
    if isinstance(found, int):
        local = found - (start - span[0])
        if 0 <= local < len(word):
            return local, False
        return None, True
    if isinstance(found, list):
        local = [o - (start - span[0]) for o in found if 0 <= o - (start - span[0]) < len(word)]
        return (local or None), not local
    return None, False


def stress_segment(text: str, *, resolver: Resolver) -> tuple[list[StressMark], dict]:
    """Marks for one segment, and a report of which layer answered for what."""
    words = find_words(text)
    marks: dict[int, StressMark] = {}
    homographs: dict[int, list[int]] = {}
    pending: list[int] = []

    def put(i: int, offset: int, source: str, confidence: float = 1.0) -> None:
        s, e = words[i]
        marks[i] = StressMark(s, e, offset, source, confidence)

    for i, (s, e) in enumerate(words):
        word = text[s:e]
        if ACUTE in word:
            offset = _offset_of_text_mark(word)
            if offset is not None:
                put(i, offset, "text")
            continue
        lower = word.lower()
        author = resolver.author_offset(lower)
        if author is not None:
            put(i, author[0], "author", author[1])
            continue
        if "ё" in lower:
            put(i, lower.index("ё"), "rule")
            continue
        found, silent = _lookup_with_compound(text, s, e, resolver.dict_lookup)
        if isinstance(found, int) and _valid_offset(lower, found):
            put(i, found, "dict")
            continue
        if silent:
            continue
        if found is None:
            found, silent = _lookup_with_compound(text, s, e, resolver.forms_lookup)
            if isinstance(found, int) and _valid_offset(lower, found):
                put(i, found, "wiktionary")
                continue
            if silent:
                continue
        if isinstance(found, list) and found:
            homographs[i] = found
        pending.append(i)

    if pending and resolver.context is not None:
        answers = resolver.context(text) or {}
        for i in pending:
            offset = answers.get(i)
            word = text[words[i][0] : words[i][1]]
            if not _valid_offset(word, offset):
                continue
            confidence = 0.9
            if i in homographs and offset not in homographs[i]:
                confidence = 0.5
            put(i, offset, "context", confidence)

    left = [i for i in pending if i not in marks]
    if left and resolver.residue is not None:
        answers = resolver.residue([(i, text[words[i][0] : words[i][1]]) for i in left]) or {}
        for i in left:
            offset = answers.get(i)
            if _valid_offset(text[words[i][0] : words[i][1]], offset):
                put(i, offset, "residue", 0.8)

    ordered = [marks[i] for i in sorted(marks)]
    counts = {name: 0 for name in ("text", "author", "rule", "dict", "wiktionary", "context", "residue")}
    for mark in ordered:
        counts[mark.source] += 1
    unresolved = [text[words[i][0] : words[i][1]] for i in pending if i not in marks]
    counts["unresolved"] = len(unresolved)
    report = {
        "counts": counts,
        "unresolved": unresolved,
        "homographs": [text[words[i][0] : words[i][1]] for i in sorted(homographs)],
    }
    return ordered, report
