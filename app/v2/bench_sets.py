"""Which байки the prompt may be shaped on, and where they live.

The benchmark and the runner that produces predictions for it both need the same
split, and it has to be defined once: a runner that quietly reads a held-out chapter
while a prompt is being tuned turns the held-out set into a second tuning set, and
the final measurement stops meaning anything.
"""
from __future__ import annotations

import pathlib

DEFAULT_DIR = pathlib.Path("data/ground_truth")

# Agreed with the owner: shape the prompt on these three, measure on the rest.
TUNING = ("Байка 3 - Рыцарь и ведьма", "Байка 22 - Четвертое Вторжение", "Байка 54 - Последний нактархим")


def chapter_names(directory: pathlib.Path, which: str) -> list[str] | None:
    """Chapter stems for a set name; None means «all of them», as the benchmark reads it."""
    if which == "tuning":
        return list(TUNING)
    if which == "held-out":
        return [p.stem for p in sorted(directory.glob("*.docx")) if p.stem not in TUNING]
    if which == "all":
        return None
    raise ValueError(f"неизвестный набор: {which}")
