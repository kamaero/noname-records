"""Import a character_legend.html into the author profile (reply colour per character)."""
import argparse

from app.db import SessionLocal
from app.services import author_profile
from app.services.author_imports.legend import parse_legend_html, import_legend


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--author", required=True)
    args = ap.parse_args()
    name_to_color = parse_legend_html(open(args.path, encoding="utf-8").read())
    with SessionLocal() as db:
        author = author_profile.get_or_create_author(db, args.author, args.author)
        summary = import_legend(db, author.id, name_to_color)
        db.commit()
    print(f"legend: names={len(name_to_color)} {summary}")
    for c in summary["conflicts"]:
        print("  CONFLICT:", c)


if __name__ == "__main__":
    main()
