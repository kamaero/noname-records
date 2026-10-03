"""Assign an existing book to an author profile (sets script_books.author_id)."""
import argparse

from app.db import SessionLocal
from app.models import ScriptBook
from app.services import author_profile


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-id", required=True)
    ap.add_argument("--author", required=True)
    args = ap.parse_args()
    with SessionLocal() as db:
        author = author_profile.get_or_create_author(db, args.author, args.author)
        book = db.query(ScriptBook).filter(ScriptBook.id == args.book_id).first()
        if not book:
            raise SystemExit(f"book not found: {args.book_id}")
        book.author_id = author.id
        db.commit()
        print(f"assigned book {book.id} -> author {author.slug} ({author.id})")


if __name__ == "__main__":
    main()
