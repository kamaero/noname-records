"""Every inflected form Wiktionary knows, with its stress — the layer between the dictionaries and RUAccent.

The base dictionary holds headwords: «толи́ка», not «толику», «толики», «толикой».
A form it did not list went straight to RUAccent, a model that reads the sentence and
guesses — and a guess is not the same twice. In «Крыльях» it gave «толи́ку яда» and,
two lines later, «то́лику терпения»; the actor recording Пупип read the second one.
A word with one reading must be looked up, not guessed, so the forms go here.

The data is Wiktextract's dump of the English Wiktionary's Russian entries
(kaikki.org, CC BY-SA): the declension and conjugation tables with stress marks.
`scripts/v2/build_stress_forms.py` turns the 900 MB JSONL into a SQLite table
`word → offsets` (~1M forms, ~30 MB) that is looked up by index and never loaded whole.

What is not taken, and why:
  * forms tagged obsolete, dated, nonstandard, pre-reform and the like — a colloquial
    «зво́нит» next to «звони́т» would make a plain word look like a homograph;
  * multiword forms («бу́ду чита́ть»), Latin transliteration, one-vowel words;
  * a proper name's form where a common word has the same letters: «То́лику» is the
    dative of Толик, and it made «толику» look ambiguous — a name counts only where
    no common word has that form («Москву́» still does);
  * a «ё» form is also stored under its «е» spelling, since books print «еще» for
    «ещё». When that spelling is a form of its own too, the two readings are merged
    and the word is a homograph: in a book that drops «ё», «моем» is «моём» as often
    as «мо́ем», and only the sentence can tell. Measured on «Крыльях», where keeping
    them apart made «мо́ем», «мо́лодежь», «черны́х» out of «моём», «молодёжь», «чёрных».

A form two lemmas stress differently (за́мок / замо́к) comes back as a list and goes
on to RUAccent as a homograph, the job RUAccent is actually for.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

ACUTE = "́"
GRAVE = "̀"
VOWELS = "аеёиоуыэюя"

# A form is a word, or words joined by hyphens: lower-case Cyrillic only once the
# marks are gone, so Latin transliteration and pre-reform «ѣ»/«і» fall out here too.
_FORM_RE = re.compile(r"^[а-яё]+(?:-[а-яё]+)*$")

# Wiktextract's bookkeeping rows, and forms that are not how the language speaks now.
SKIP_TAGS = frozenset({
    "romanization", "transliteration", "table-tags", "inflection-template", "class",
    "obsolete", "archaic", "dated", "nonstandard", "proscribed", "pre-reform",
    "dialectal", "misspelling", "rare", "poetic", "colloquial", "vernacular",
})

# Where the built table lives unless STRESS_FORMS_DB_PATH says otherwise.
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "stress_forms.sqlite"

FormsLookup = Callable[[str], "int | list[int] | None"]


def _offsets(form: str) -> tuple[str, set[int]] | None:
    """(plain lower-case word, stressed vowel offsets) for one marked form, or None."""
    plain: list[str] = []
    offsets: set[int] = set()
    for ch in form.strip().lower():
        if ch == ACUTE:
            if plain:
                offsets.add(len(plain) - 1)
            continue
        if ch == GRAVE:  # secondary stress in a compound: not the one a reader hears
            continue
        plain.append(ch)
    word = "".join(plain)
    if not _FORM_RE.match(word) or sum(ch in VOWELS for ch in word) < 2:
        return None
    if not offsets and "ё" in word:
        offsets = {word.index("ё")}
    offsets = {o for o in offsets if word[o] in VOWELS}
    return (word, offsets) if offsets else None


def forms_from_entry(entry: dict) -> Iterator[tuple[str, set[int]]]:
    """Every (word, offsets) one Wiktextract entry gives."""
    for item in entry.get("forms") or ():
        tags = set(item.get("tags") or ())
        if tags & SKIP_TAGS:
            continue
        found = _offsets(str(item.get("form") or ""))
        if found is not None:
            yield found


def collect(entries: Iterable[dict]) -> dict[str, set[int]]:
    """Word → every offset any entry stresses it on, «е» spellings of «ё» forms included."""
    table: dict[str, set[int]] = {}
    names: dict[str, set[int]] = {}
    for entry in entries:
        into = names if entry.get("pos") == "name" else table
        for word, offsets in forms_from_entry(entry):
            into.setdefault(word, set()).update(offsets)
    for word, offsets in names.items():
        if word not in table:
            table[word] = offsets
    folded: dict[str, set[int]] = {}
    for word, offsets in table.items():
        if "ё" in word:
            folded.setdefault(word.replace("ё", "е"), set()).update(offsets)
    for plain, offsets in folded.items():
        table.setdefault(plain, set()).update(offsets)  # «еще» gets «ещё»; «моем» becomes ambiguous
    return table


def _read_jsonl(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def build_forms_db(source: Path | str, target: Path | str) -> dict:
    """Write the SQLite table from a Wiktextract JSONL. Replaces `target` atomically."""
    source, target = Path(source), Path(target)
    table = collect(_read_jsonl(source))
    partial = target.with_suffix(target.suffix + ".partial")
    partial.unlink(missing_ok=True)
    with sqlite3.connect(partial) as db:
        db.execute("CREATE TABLE forms (word TEXT PRIMARY KEY, offsets TEXT NOT NULL) WITHOUT ROWID")
        db.executemany(
            "INSERT INTO forms VALUES (?, ?)",
            ((word, ",".join(str(o) for o in sorted(offsets))) for word, offsets in table.items()),
        )
        db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        homographs = sum(1 for offsets in table.values() if len(offsets) > 1)
        stats = {"source": source.name, "forms": len(table), "homographs": homographs}
        db.executemany("INSERT INTO meta VALUES (?, ?)", ((k, str(v)) for k, v in stats.items()))
    partial.replace(target)
    return stats


def open_lookup(path: Path | str) -> FormsLookup:
    """A lookup over a built table; a missing or unreadable file means the layer is off."""
    path = Path(path)
    lock = threading.Lock()
    try:
        # read-only and shared across the worker's threads; every query takes the lock
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        db.execute("SELECT 1 FROM forms LIMIT 1")
    except sqlite3.Error as exc:
        if path.exists():
            logger.warning("stress forms table unreadable, layer skipped: %s (%s)", path, exc)
        return lambda word: None

    @lru_cache(maxsize=65536)
    def lookup(word: str):
        with lock:
            row = db.execute("SELECT offsets FROM forms WHERE word = ?", ((word or "").lower(),)).fetchone()
        if row is None:
            return None
        offsets = [int(o) for o in row[0].split(",") if o]
        if not offsets:
            return None
        return offsets[0] if len(offsets) == 1 else offsets

    return lookup


_DEFAULT: FormsLookup | None = None
_DEFAULT_LOCK = threading.Lock()


def default_forms_lookup(word: str):
    """The table at STRESS_FORMS_DB_PATH, or `data/stress_forms.sqlite` — opened on first use."""
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                configured = (settings.stress_forms_db_path or "").strip()
                _DEFAULT = open_lookup(Path(configured) if configured else DEFAULT_PATH)
    return _DEFAULT(word)
