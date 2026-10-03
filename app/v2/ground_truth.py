"""A read chapter into the records the attribution benchmark scores.

Only two things in the markup are evidence, and both come from the owner: a coloured
run is that character speaking, and a paragraph carrying no colour at all is the
Narrator — always and only. Everything else is dropped:

  * an uncoloured stretch beside a coloured one. Whether «— сказал он» was painted
    with the replica was settled at the mixing desk, not at markup time — such
    clauses are cut when the actor already plays them and kept when they carry
    meaning — so the span is ambiguous between the Narrator and the speaker's aside.
  * a colour the chapter's own legend never introduced. Colours are reused between
    tales; only the cycle's recurring leads keep one, so a missing colour cannot be
    borrowed from a neighbouring chapter. The legend is built per chapter and what it
    does not name is not guessed.

One colour may stand for several roles: the owner colours per actor, and an actor
voicing three parts paints all three the same. Such a span cannot say which of the
three spoke, so every role under that colour is recorded as acceptable
(`alternatives`) and the scorer takes any of them. The same for a legend line
«Рассказчик — Инвнехлизад»: the frame narrator is a character, and naming him is not
a mistake. Measured over the cycle: 104 of 899 legend lines sit behind a shared colour.

Every drop is counted and returned alongside the records. A benchmark that quietly
shrinks its own denominator reports a number that cannot be wrong, which is worse
than reporting nothing.
"""
from __future__ import annotations

NARRATOR = "Рассказчик"


_DIALOGUE_START = ("–", "—", "-")

# Below this share of coloured dialogue a file is a manuscript, not a markup: the
# owner had not yet painted it. Fourteen of the 67 files carry no legend and no
# colour at all; scoring them would grade the model against blank paper.
MIN_COLOURED_DIALOGUE = 0.5


def markup_quality(chapter) -> dict:
    """How much of this file the owner actually marked: legend size, dialogue coloured."""
    dialogue = coloured = 0
    for para in chapter.paragraphs:
        if not para.text.lstrip().startswith(_DIALOGUE_START):
            continue
        dialogue += 1
        if any(span.speaker_colour for span in para.spans):
            coloured += 1
    share = coloured / dialogue if dialogue else 0.0
    return {"legend": len(chapter.legend), "dialogue": dialogue, "coloured": coloured, "share": share}


def is_marked(chapter, *, min_share: float = MIN_COLOURED_DIALOGUE) -> bool:
    quality = markup_quality(chapter)
    return quality["legend"] > 0 and quality["dialogue"] > 0 and quality["share"] >= min_share


def acceptable_roles(chapter, colour: str) -> list[str]:
    """Every role the markup could mean by this colour, the legend's last one first."""
    entries = (getattr(chapter, "legend_all", None) or {}).get(colour) or []
    last = chapter.legend.get(colour)
    roles: list[str] = []
    for entry in ([last] if last else []) + list(entries):
        role = (entry.role or "").strip()
        if role and role not in roles:
            roles.append(role)
        # «Рассказчик — Инвнехлизад — actor»: the note names the character who narrates.
        note = (entry.note or "").strip()
        if role == NARRATOR and note and note not in roles:
            roles.append(note)
    return roles or [NARRATOR]


def ground_truth_records(chapter) -> tuple[list[dict], dict]:
    """`(records, stats)` — the scorable spans, and an account of everything else."""
    records: list[dict] = []
    stats = {
        "chapter": chapter.title,
        "path": chapter.path,
        "paragraphs": len(chapter.paragraphs),
        "spans_total": 0,
        "scored": 0,
        "dropped_uncertain": 0,
        "dropped_no_legend": 0,
        "colours_without_legend": {},
    }

    for para in chapter.paragraphs:
        for span in para.spans:
            stats["spans_total"] += 1

            if not span.certain:
                stats["dropped_uncertain"] += 1
                continue

            if span.speaker_colour is None:
                speaker, actor, alternatives = NARRATOR, "", [NARRATOR]
            else:
                entry = chapter.legend.get(span.speaker_colour)
                if entry is None or not entry.role:
                    stats["dropped_no_legend"] += 1
                    key = span.speaker_colour
                    stats["colours_without_legend"][key] = stats["colours_without_legend"].get(key, 0) + 1
                    continue
                speaker, actor = entry.role, entry.actor
                alternatives = acceptable_roles(chapter, span.speaker_colour)

            records.append({
                "chapter": chapter.title,
                "ordinal": para.ordinal,
                "text": para.text,
                "span_start": span.start,
                "span_end": span.end,
                "span_text": para.text[span.start:span.end],
                "speaker": speaker,
                "alternatives": alternatives,
                "actor": actor,
            })
            stats["scored"] += 1

    return records, stats
