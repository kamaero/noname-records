"""Score an attribution against the owner's markup.

Measured per character of text, not per paragraph and not per span, and the reason is
the markup itself: the owner painted each replica's boundary by hand and decided at
the mixing desk whether «— сказал он» belonged inside it. A model that names the right
speaker but ends the span two words early is right about the thing that matters, and
per-character scoring says so where per-span scoring would fail the whole replica.

Only characters covered by a certain ground-truth span are scored. What the markup
declines to assert — the ambiguous tail beside a replica, a colour its legend never
introduced — is absent from the records and so from both numerator and denominator: a
prediction there is neither rewarded nor punished.

Everything the scorer could not place is counted and returned. A benchmark that
silently drops what it does not understand reports a number that cannot be wrong.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

from app.services.author_profile import normalize_name


def _raw_key(name) -> str | None:
    """A legend spelling as its own identity, for names the workbook does not carry.

    «Банкир Кдерк» is how one chapter's legend names a part the cast workbook lists
    under another name, if at all. The resolver cannot place it, but the markup and
    the model both used that spelling, and agreeing on it is not an error.
    """
    key = normalize_name(str(name or ""))
    return f"raw:{key}" if key else None


@dataclass
class Report:
    covered_chars: int = 0
    correct_chars: int = 0
    # Characters the model declined to name (UNSURE or nothing at all). Reported apart
    # from the errors: an abstention is a question for the operator, a wrong name is
    # a wrong take in the studio.
    abstained_chars: int = 0
    per_role: dict[str, dict[str, float]] = field(default_factory=dict)
    disagreements: list[dict] = field(default_factory=list)
    unresolved_predictions: collections.Counter = field(default_factory=collections.Counter)
    unresolved_gold: collections.Counter = field(default_factory=collections.Counter)

    @property
    def accuracy(self) -> float:
        """Zero when nothing was covered — an empty run has not proved anything."""
        return self.correct_chars / self.covered_chars if self.covered_chars else 0.0

    @property
    def abstention(self) -> float:
        return self.abstained_chars / self.covered_chars if self.covered_chars else 0.0

    @property
    def answered_accuracy(self) -> float:
        """Accuracy over what the model did name — the studio-take error rate."""
        answered = self.covered_chars - self.abstained_chars
        return self.correct_chars / answered if answered else 0.0


def _shown(name: str) -> str:
    """A raw key back to a readable spelling for reports."""
    return name[4:] if name.startswith("raw:") else name


def _lay_out(records, resolver, unplaced) -> dict[tuple, list[str | None]]:
    """Per paragraph, a list as long as its text holding the speaker at each position."""
    canvas: dict[tuple, list[str | None]] = {}
    for record in records:
        key = (record["chapter"], record["ordinal"])
        text = record["text"]
        row = canvas.setdefault(key, [None] * len(text))
        speaker = resolver.resolve(record["speaker"])
        if speaker is None:
            unplaced[record["speaker"]] += 1
            # UNSURE is an admission, not a name: it never matches anything.
            speaker = None if str(record["speaker"]).strip().upper() == "UNSURE" else _raw_key(record["speaker"])
            if speaker is None:
                continue
        for position in range(max(0, record["span_start"]), min(len(row), record["span_end"])):
            row[position] = speaker
    return canvas


def _lay_out_gold(records, resolver, unplaced) -> dict[tuple, list[tuple[str, ...] | None]]:
    """Like `_lay_out`, but each position holds every acceptable name, first = canonical.

    A record's `alternatives` are the roles the markup could mean (one colour, several
    parts). A name the cast cannot place is dropped from the tuple; a record whose
    names all fail is counted unplaced and scores nothing, as before.
    """
    canvas: dict[tuple, list[tuple[str, ...] | None]] = {}
    for record in records:
        key = (record["chapter"], record["ordinal"])
        text = record["text"]
        row = canvas.setdefault(key, [None] * len(text))
        names: list[str] = []
        raw_names = [record["speaker"]] + [a for a in (record.get("alternatives") or []) if a != record["speaker"]]
        for raw in raw_names:
            resolved = resolver.resolve(raw)
            if resolved and resolved not in names:
                names.append(resolved)
        if not names:
            unplaced[record["speaker"]] += 1
        # The legend's own spellings are acceptable too, after the canonical ones.
        for raw in raw_names:
            key = _raw_key(raw)
            if key and key not in names:
                names.append(key)
        if not names:
            continue
        accepted = tuple(names)
        for position in range(max(0, record["span_start"]), min(len(row), record["span_end"])):
            row[position] = accepted
    return canvas


def score(gold_records, predicted_records, *, resolver, max_disagreements: int = 200) -> Report:
    report = Report()
    gold = _lay_out_gold(gold_records, resolver, report.unresolved_gold)
    predicted = _lay_out(predicted_records, resolver, report.unresolved_predictions)

    texts = {(r["chapter"], r["ordinal"]): r["text"] for r in gold_records}
    counts: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0}
    )
    # One entry per (paragraph, expected, predicted) rather than per character, so a
    # disagreement reads as a sentence a person can check.
    seen_disagreement: set[tuple] = set()

    for key, gold_row in gold.items():
        predicted_row = predicted.get(key) or []
        for position, accepted in enumerate(gold_row):
            if accepted is None:
                continue
            report.covered_chars += 1
            actual = predicted_row[position] if position < len(predicted_row) else None
            if actual is not None and actual in accepted:
                report.correct_chars += 1
                counts[_shown(actual)]["tp"] += 1
                continue
            expected = "|".join(_shown(n) for n in accepted if not n.startswith("raw:")) or _shown(accepted[0])
            counts[_shown(accepted[0])]["fn"] += 1
            if actual is None:
                report.abstained_chars += 1
            else:
                counts[_shown(actual)]["fp"] += 1
            actual = _shown(actual) if actual is not None else None
            signature = (key, expected, actual)
            if signature not in seen_disagreement and len(report.disagreements) < max_disagreements:
                seen_disagreement.add(signature)
                report.disagreements.append({
                    "chapter": key[0],
                    "ordinal": key[1],
                    "expected": expected,
                    "predicted": actual,
                    "text": texts.get(key, "")[:220],
                })

    for role, tally in counts.items():
        tp, fp, fn = tally["tp"], tally["fp"], tally["fn"]
        report.per_role[role] = {
            "chars": tp + fn,
            "precision": tp / (tp + fp) if (tp + fp) else 0.0,
            "recall": tp / (tp + fn) if (tp + fn) else 0.0,
        }
    for role, values in report.per_role.items():
        precision, recall = values["precision"], values["recall"]
        values["f1"] = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return report
