"""Read the owner's hand-marked «Полумракские байки» docx into ground truth.

Each role's replicas carry that role's colour; a legend at the top of the chapter
maps colour to role and role to actor. Two guarantees from the owner hold, and only
these are scored: a coloured run is that character speaking, and a paragraph with no
colour anywhere is the Narrator.

What is deliberately not a signal: whether an attribution clause inside a replica
(«— сказал он») is coloured. That was a mixing-time decision — such clauses are cut
when the actor already plays them and kept when they carry meaning — so an uncoloured
stretch inside a partly coloured paragraph is ambiguous between the Narrator and the
speaker's own aside. It is kept, marked uncertain, and left out of scoring.

The docx reading lives in `read_chapter`; everything above it is pure so the awkward
cases — and they are all taken from the real files — can be tested directly.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass, field

from docx import Document
from docx.oxml.ns import qn

# Roles and actors are separated by a dash with space around it, or by a dash glued
# to the end of the line when the actor is missing («Банкир Субиух-»).
_DASH_SPLIT = re.compile(r"\s+[-–—]\s+")
_TRAILING_DASH = re.compile(r"\s*[-–—]\s*$")


@dataclass(frozen=True)
class LegendEntry:
    role: str
    actor: str = ""
    note: str = ""


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    speaker_colour: str | None
    certain: bool


def parse_legend_line(text: str, *, coloured_text: str = "") -> LegendEntry:
    """One legend line into role, actor and whatever sits between them.

    When the colour covers only part of the line the coloured part names the role:
    «Рассказчик (Дегатти в интерлюдии) - Роман Сомов» is Дегатти's colour, not the
    narrator's. When it covers the whole line the role is what precedes the first
    dash, and the actor what follows the last — a line may carry three parts, as in
    «Рассказчик - Инвнехлизад - Евгений Остапович», where the middle says who the
    narrator is rather than who voices them.
    """
    line = _TRAILING_DASH.sub("", (text or "").strip())
    parts = [p.strip() for p in _DASH_SPLIT.split(line) if p.strip()]
    if not parts:
        return LegendEntry(role="")

    role = parts[0]
    actor = parts[-1] if len(parts) > 1 else ""
    note = " ".join(parts[1:-1]) if len(parts) > 2 else ""

    marked = (coloured_text or "").strip()
    if marked and marked != line and marked in line:
        role = _TRAILING_DASH.sub("", marked)

    return LegendEntry(role=role, actor=actor, note=note)


def spans_from_runs(runs: list[tuple[str | None, str]]) -> list[Span]:
    """Runs of one paragraph into spans addressed by offsets into its text.

    Adjacent runs of the same colour merge, because Word splits a sentence into runs
    for reasons of its own — a spell-check boundary is not a change of speaker.

    A span is `certain` when the ground truth actually asserts something: a coloured
    run, or an uncoloured paragraph that carries no colour at all. An uncoloured
    stretch sitting next to a coloured one is not.
    """
    text_len = sum(len(text or "") for _colour, text in runs)
    if not text_len:
        return []

    has_colour = any(colour for colour, _text in runs)

    merged: list[tuple[str | None, int, int]] = []
    offset = 0
    for colour, text in runs:
        length = len(text or "")
        if not length:
            continue
        if merged and merged[-1][0] == colour:
            merged[-1] = (colour, merged[-1][1], offset + length)
        else:
            merged.append((colour, offset, offset + length))
        offset += length

    return [
        Span(start=start, end=end, speaker_colour=colour, certain=bool(colour) or not has_colour)
        for colour, start, end in merged
    ]


# A legend line is short and ENDS with a dash; a line of dialogue also carries a dash
# but STARTS with it, and prose carries none. Reading that backwards swallows the whole
# tale into the legend.
_LEGEND_MAX_CHARS = 120
_DIALOGUE_START = tuple("–—-")


def _looks_like_legend_line(text: str) -> bool:
    line = (text or "").strip()
    if not line or len(line) > _LEGEND_MAX_CHARS:
        return False
    if line.startswith(_DIALOGUE_START):
        return False
    return bool(_TRAILING_DASH.search(line) or _DASH_SPLIT.search(line))


def legend_bounds(paragraph_texts: list[str]) -> tuple[int, int]:
    """Half-open range of the paragraphs that make up the cast legend.

    Blank lines inside the legend are tolerated; the run ends at the first non-empty
    paragraph that does not look like a legend line. A chapter without a legend
    returns an empty range rather than guessing.
    """
    start = None
    end = None
    for index, text in enumerate(paragraph_texts):
        if not (text or "").strip():
            continue
        if _looks_like_legend_line(text):
            if start is None:
                start = index
            end = index + 1
        elif start is not None:
            break
    if start is None:
        return (0, 0)
    return (start, end)


def _colour_key(run) -> str | None:
    """The mark on a run, as `H:<name>` for a highlight or `S:<hex>` for shading.

    Both are used: the owner marked with the highlighter and with cell shading, and
    Google Docs exports them into different attributes. Font colour is not used at
    all in these files, so it is deliberately ignored rather than guessed at.
    """
    rpr = run._element.find(qn("w:rPr"))
    if rpr is None:
        return None
    node = rpr.find(qn("w:highlight"))
    if node is not None:
        value = node.get(qn("w:val"))
        if value and value != "none":
            return f"H:{value}"
    node = rpr.find(qn("w:shd"))
    if node is not None:
        value = node.get(qn("w:fill"))
        if value and value not in ("auto", "FFFFFF", "ffffff"):
            return f"S:{value.lower()}"
    return None


@dataclass(frozen=True)
class Paragraph:
    ordinal: int
    text: str
    spans: list[Span]


@dataclass
class Chapter:
    path: str
    title: str
    heading: str = ""
    legend: dict[str, LegendEntry] = field(default_factory=dict)
    # Every legend line per colour, in order. The owner gives one colour per ACTOR, so
    # an actor voicing several parts leaves several roles under one colour; `legend`
    # keeps the last of them, this keeps them all. Measured: 104 of 899 legend lines
    # across the cycle are hidden that way, and they are the big parts.
    legend_all: dict[str, list[LegendEntry]] = field(default_factory=dict)
    paragraphs: list[Paragraph] = field(default_factory=list)
    colours_without_legend: dict[str, int] = field(default_factory=dict)


def read_chapter(path) -> Chapter:
    """One marked-up docx into legend plus paragraphs carrying coloured spans."""
    document = Document(str(path))
    paragraphs = document.paragraphs
    texts = [p.text for p in paragraphs]
    start, end = legend_bounds(texts)
    # The tale begins after the legend. Everything above it — the heading — is not
    # narration and nobody reads it aloud, but it was landing in the body and giving
    # every chapter one spurious Narrator span.
    body_from = end if end else 1
    heading = next((t.strip() for t in texts[:max(1, start)] if t.strip()), "")

    legend: dict[str, LegendEntry] = {}
    legend_all: dict[str, list[LegendEntry]] = {}
    for para in paragraphs[start:end]:
        if not para.text.strip():
            continue
        marked = [(_colour_key(r), r.text) for r in para.runs if r.text]
        colours = {c for c, _t in marked if c}
        if len(colours) != 1:
            # No colour, or a line carrying several: nothing unambiguous to record.
            continue
        colour = next(iter(colours))
        coloured_text = "".join(t for c, t in marked if c == colour)
        entry = parse_legend_line(para.text, coloured_text=coloured_text)
        legend[colour] = entry
        legend_all.setdefault(colour, []).append(entry)

    body: list[Paragraph] = []
    orphans: dict[str, int] = {}
    ordinal = 0
    for index, para in enumerate(paragraphs):
        if index < body_from or not para.text.strip():
            continue
        runs = [(_colour_key(r), r.text) for r in para.runs if r.text]
        spans = spans_from_runs(runs)
        if not spans:
            continue
        for span in spans:
            if span.speaker_colour and span.speaker_colour not in legend:
                orphans[span.speaker_colour] = orphans.get(span.speaker_colour, 0) + 1
        body.append(Paragraph(ordinal=ordinal, text=para.text, spans=spans))
        ordinal += 1

    return Chapter(
        path=str(path),
        # From the file name, not the document: Байка 18's own first paragraph reads
        # «к Император», and the title keys every ground-truth record.
        title=pathlib.Path(str(path)).stem,
        heading=heading,
        legend=legend,
        legend_all=legend_all,
        paragraphs=body,
        colours_without_legend=orphans,
    )
