"""Import the author's stress list into data/author_stress/<world>.json and, on request,
into `author_pronunciations`.

The JSON is the artefact the stress layer reads (`app/v2/stress.py`,
`author_map_from_entries`) and the one a human can diff; the table is what the old
pipeline and the UI already consult, so filling it makes the author's list count in
both worlds. Rows are matched on (author_id, term) and updated in place, with
`source="import_gdoc"` so a later import knows which rows it owns.

Refuses the production database unless told twice, like every v2 script: the layer
is built beside the running system, not inside it.

    python scripts/v2/import_author_stress.py                       # JSON only
    python scripts/v2/import_author_stress.py --db copy.db --author-id <id> --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.v2.author_stress import AuthorStress, read_author_stress_docx, stressed_form

REPO = pathlib.Path(__file__).resolve().parents[2]
PROD_DB = "noname.db"
DEFAULT_DOCX = ""
DEFAULT_OUT = REPO / "data" / "author_stress" / "polumrak.json"
SOURCE = "import_gdoc"


def entries_to_json(entries: list[AuthorStress], *, docx_name: str, author_id: str, stats: dict) -> dict:
    return {
        "source": docx_name,
        "author_id": author_id,
        "entries": [
            {"word": e.word_lower, "vowel_index": e.vowel_index, "stressed": stressed_form(e), "note": e.note}
            for e in entries
        ],
        "stats": {k: v for k, v in stats.items() if k != "unknown_words"},
        "unknown_words": stats.get("unknown_words", []),
    }


def upsert_author_pronunciations(db, *, author_id: str, entries: list[AuthorStress]) -> dict:
    """Write the entries as `AuthorPronunciation` rows. Returns {"created", "updated", "unchanged"}."""
    from app.models import AuthorPronunciation

    existing = {
        (row.term or "").strip().lower(): row
        for row in db.query(AuthorPronunciation).filter(AuthorPronunciation.author_id == author_id).all()
    }
    counts = {"created": 0, "updated": 0, "unchanged": 0}
    for entry in entries:
        stressed = stressed_form(entry)
        row = existing.get(entry.word_lower)
        if row is None:
            db.add(AuthorPronunciation(author_id=author_id, term=entry.word_lower, stressed=stressed, variants="[]", source=SOURCE))
            counts["created"] += 1
        elif (row.stressed or "") != stressed or row.source != SOURCE:
            row.stressed = stressed
            row.source = SOURCE
            counts["updated"] += 1
        else:
            counts["unchanged"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docx", default=DEFAULT_DOCX)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--db", help="sqlite file; when given, author_pronunciations is upserted")
    parser.add_argument("--author-id", default="")
    parser.add_argument("--dry-run", action="store_true", help="report only; write neither the JSON nor the table")
    parser.add_argument("--i-mean-production", action="store_true")
    args = parser.parse_args()

    if args.db and os.path.realpath(args.db) == os.path.realpath(PROD_DB) and not args.i_mean_production:
        print("ОТКАЗ: это продовая база. v2 строится рядом с работающей системой, а не в ней.")
        return 2
    if args.db and not args.author_id:
        print("ОТКАЗ: с --db нужен --author-id.")
        return 2

    entries, stats = read_author_stress_docx(args.docx)
    print(f"docx: {args.docx}")
    print(f"paragraphs {stats['paragraphs']}, section {stats['section_start']}..{stats['section_end']} (end: {stats['end_reason']})")
    print(f"entries {stats['entries']}, unknown {stats['unknown']}, duplicates {stats['duplicates']}, with note {stats['notes']}")
    if stats["unknown_words"]:
        print("unknown (no internal capital):", ", ".join(stats["unknown_words"]))

    payload = entries_to_json(entries, docx_name=os.path.basename(args.docx), author_id=args.author_id, stats=stats)
    if args.dry_run:
        print(f"dry-run: would write {len(entries)} entries to {args.out}")
    else:
        out = pathlib.Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {out}")

    if args.db:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(f"sqlite:///{args.db}")
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        with SessionLocal() as db:
            counts = upsert_author_pronunciations(db, author_id=args.author_id, entries=entries)
            if args.dry_run:
                db.rollback()
                print(f"dry-run: author_pronunciations would be created {counts['created']}, updated {counts['updated']}, unchanged {counts['unchanged']}")
            else:
                db.commit()
                print(f"author_pronunciations: created {counts['created']}, updated {counts['updated']}, unchanged {counts['unchanged']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
