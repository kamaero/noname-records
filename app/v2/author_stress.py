"""The author's own stress list, read from the notation he actually uses.

For a fantasy world the dictionaries are silent: «Полумрак», «Бимькмолепус» and a few
hundred more exist only in these books, and the only authority on how they sound is
the author. He keeps a list in a Google Doc — one name per paragraph, the stressed
vowel written as a CAPITAL letter inside the word (`АбхилагАша`). That notation is
the input; a lowercase word plus the index of its stressed vowel is the output, so
the rest of the stress layer never has to know how the list was typed.

The list was typed by a person and is read as such: two forms with a slash, a note
in parentheses or after a spaced dash, a non-breaking hyphen between parts, a
trailing comma, two names in one paragraph. A name whose only capital is its first
letter (`Изельд`) says nothing about stress and is skipped but counted, so the report
can say how many names still need an answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

VOWELS_LOWER = "аеёиоуыэюя"
ACUTE = "́"

# Everything that separates one word from the next inside an entry: spaces, all the
# hyphen look-alikes people type, apostrophes, commas and line breaks.
_HYPHENS = "‐‑‒–—-"
_WORD_RE = re.compile(r"[А-Яа-яЁё]+")
_NOTE_PAREN_RE = re.compile(r"\(([^)]*)\)")
_NOTE_DASH_RE = re.compile(r"\s[-–—]\s")
_KNOWN_HEADINGS = {"в ролях", "титры", "чтецы", "роли", "актёры", "актеры", "ударения"}


@dataclass(frozen=True)
class AuthorStress:
    word_lower: str
    vowel_index: int  # character index of the stressed vowel inside word_lower
    note: str = ""


def stressed_form(entry: AuthorStress) -> str:
    """The word with U+0301 after its stressed vowel — the form `author_pronunciations` stores."""
    i = entry.vowel_index
    return entry.word_lower[: i + 1] + ACUTE + entry.word_lower[i + 1 :]


def _stress_index(word: str) -> int:
    """Index of the capital vowel that marks the stress, or -1 when the word says nothing.

    A capital at index 0 is just the initial of a name and never counts; the first
    capital vowel after it does. `КрАА` has two — the first wins.
    """
    for i, ch in enumerate(word):
        if i > 0 and ch.isupper() and ch.lower() in VOWELS_LOWER:
            return i
    return -1


def _split_note(line: str) -> tuple[str, str]:
    notes: list[str] = []
    head = line
    m = _NOTE_PAREN_RE.search(head)
    if m:
        notes.append(m.group(1).strip())
        head = head[: m.start()] + head[m.end():]
    m = _NOTE_DASH_RE.search(head)
    if m:
        notes.append(head[m.end():].strip())
        head = head[: m.start()]
    return head, "; ".join(n for n in notes if n)


def _parse_line(line: str) -> tuple[list[AuthorStress], list[str]]:
    """Entries with a known stress, and the surface words that had none."""
    head, note = _split_note(line or "")
    known: list[AuthorStress] = []
    unknown: list[str] = []
    for word in _WORD_RE.findall(head):
        idx = _stress_index(word)
        if idx < 0:
            unknown.append(word)
            continue
        known.append(AuthorStress(word.lower(), idx, note))
    return known, unknown


def parse_author_notation(line: str) -> list[AuthorStress]:
    """One paragraph of the list → its entries. Words without an internal capital are dropped."""
    return _parse_line(line)[0]


def _is_heading(text: str) -> bool:
    stripped = text.strip().rstrip(":").strip()
    if not stripped:
        return False
    if stripped.casefold() in _KNOWN_HEADINGS:
        return True
    letters = [ch for ch in stripped if ch.isalpha()]
    return bool(letters) and not any(ch.islower() for ch in letters)


def read_author_stress_docx(path: str | Path) -> tuple[list[AuthorStress], dict]:
    """Read the «Ударения» section of the author's document.

    The section runs from the paragraph «Ударения» to the next heading — a known one
    such as «В ролях», or any paragraph with letters but no lowercase — or to two
    consecutive empty paragraphs after at least one entry. Raises when the heading
    is missing rather than guessing which paragraphs are names.
    """
    import docx  # local: python-docx is only needed by the importer, not at request time

    paragraphs = [p.text for p in docx.Document(str(path)).paragraphs]
    start = next(
        (i for i, text in enumerate(paragraphs) if text.strip().rstrip(":").casefold() == "ударения"),
        None,
    )
    if start is None:
        raise ValueError(f"no «Ударения» paragraph in {path}")

    entries: list[AuthorStress] = []
    seen: set[str] = set()
    unknown_words: list[str] = []
    duplicates = 0
    notes = 0
    blank_run = 0
    end = len(paragraphs)
    end_reason = "end_of_document"
    for i in range(start + 1, len(paragraphs)):
        text = paragraphs[i]
        if not text.strip():
            blank_run += 1
            if blank_run >= 2 and entries:
                end, end_reason = i, "blank_gap"
                break
            continue
        blank_run = 0
        if _is_heading(text):
            end, end_reason = i, "heading"
            break
        known, unknown = _parse_line(text)
        unknown_words.extend(unknown)
        for entry in known:
            if entry.word_lower in seen:
                duplicates += 1
                continue
            seen.add(entry.word_lower)
            entries.append(entry)
            if entry.note:
                notes += 1

    stats = {
        "paragraphs": len(paragraphs),
        "section_start": start,
        "section_end": end,
        "end_reason": end_reason,
        "entries": len(entries),
        "unknown": len(unknown_words),
        "unknown_words": unknown_words,
        "duplicates": duplicates,
        "notes": notes,
    }
    return entries, stats
