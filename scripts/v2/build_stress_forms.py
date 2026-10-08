"""Build the Wiktionary word-form table the stress layer reads (`app/v2/stress_forms.py`).

The source is Wiktextract's Russian dump (CC BY-SA, from the English Wiktionary):

    curl -o kaikki-russian.jsonl https://kaikki.org/dictionary/Russian/kaikki.org-dictionary-Russian.jsonl
    python scripts/v2/build_stress_forms.py kaikki-russian.jsonl                 # → data/stress_forms.sqlite
    python scripts/v2/build_stress_forms.py kaikki-russian.jsonl --out /path/to/stress_forms.sqlite

The target is replaced atomically, so a running worker keeps the old table open
until it restarts rather than reading a half-written one.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.v2.stress_forms import build_forms_db, default_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="Wiktextract JSONL of the Russian entries")
    parser.add_argument("--out", default=str(default_path()), help=f"target SQLite (default {default_path()})")
    args = parser.parse_args()
    stats = build_forms_db(args.source, args.out)
    print(f"{args.out}: {stats['forms']} forms, {stats['homographs']} with more than one stress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
