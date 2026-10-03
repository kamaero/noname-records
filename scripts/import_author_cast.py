"""Import a cast-dashboard.html (CAST_DATA) into the author profile (actor per character)."""
import argparse

from app.db import SessionLocal
from app.services import author_profile
from app.services.author_imports.cast import parse_cast_html, import_cast


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--author", required=True)
    args = ap.parse_args()
    role_to_actor = parse_cast_html(open(args.path, encoding="utf-8").read())
    with SessionLocal() as db:
        author = author_profile.get_or_create_author(db, args.author, args.author)
        summary = import_cast(db, author.id, role_to_actor)
        db.commit()
    print(f"cast: roles={len(role_to_actor)} {summary}")
    for c in summary["conflicts"]:
        print("  CONFLICT:", c)


if __name__ == "__main__":
    main()
