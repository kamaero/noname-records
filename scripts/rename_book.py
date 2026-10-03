#!/usr/bin/env python
"""Переименовать книгу: автор и название порознь, витрина собирается сама.

То же, что делает карточка «Название книги» в хабе, — для книг, залитых до того,
как автор получил свою графу. Показывает код книги до и после: код выводится из
названия, и записи актёров подписаны именно им.

    python scripts/rename_book.py --book-id <id> --author Белозёровы --title "Крылья Полумрака"
    python scripts/rename_book.py --list
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from app.db import SessionLocal  # noqa: E402
from app.models import ScriptBook, ScriptLog  # noqa: E402
from app.services.audio_naming import book_token  # noqa: E402
from app.services.audio_uploads import derive_book_code  # noqa: E402
from app.services.book_title import apply_book_title, split_display_title  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book-id", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--list", action="store_true", help="показать книги и их коды")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with SessionLocal() as db:
        if args.list or not args.book_id:
            for book in db.query(ScriptBook).order_by(ScriptBook.created_at).all():
                code = book_token(derive_book_code(str(book.title or ""))) or "—"
                guess = split_display_title(str(book.title or ""))
                hint = f"  (автор в названии: {guess[0]})" if guess[0] else ""
                print(f"{book.id}  [{code}]  «{book.display_title or book.title}»{hint}")
            return 0

        book = db.get(ScriptBook, args.book_id)
        if book is None:
            print(f"Книги {args.book_id} нет", file=sys.stderr)
            return 1

        was_display = str(book.display_title or book.title or "")
        was_code = book_token(derive_book_code(str(book.title or ""))) or "—"
        # Пустые поля — берём то, что уже лежит: автора вынимаем из старого названия.
        old_author, old_title = split_display_title(str(book.title or ""))
        author = args.author or str(book.author_label or "") or old_author
        title = args.title or old_title

        display = apply_book_title(book, author=author, title=title)
        code = book_token(derive_book_code(str(book.title or ""))) or "—"
        print(f"«{was_display}» [{was_code}]  →  «{display}» [{code}]")
        if code != was_code:
            print("ВНИМАНИЕ: код книги изменился — записи актёров с прежним кодом "
                  "перестанут находиться по нему.", file=sys.stderr)
        if args.dry_run:
            db.rollback()
            return 0
        db.add(ScriptLog(book_id=book.id, level="info",
                         message=f"Книга переименована скриптом: «{was_display}» → «{display}», код — {code}"))
        db.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
