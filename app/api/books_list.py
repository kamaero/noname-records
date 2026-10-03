from __future__ import annotations

from typing import Any, Callable

from fastapi import Request

from app.api._helpers import unauthorized_response


def build_books_list_handlers(deps: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    """`GET /api/books` — the book list every surviving screen starts from."""
    is_authenticated = deps["is_authenticated"]
    session_local = deps["SessionLocal"]
    func = deps["func"]
    script_book_model = deps["ScriptBook"]
    character_model = deps["Character"]
    collect_books_progress = deps["collect_books_progress"]
    serialize_book_summary = deps["serialize_book_summary"]

    def api_books(request: Request):
        if not is_authenticated(request):
            return unauthorized_response()
        with session_local() as db:
            books = db.query(script_book_model).order_by(script_book_model.created_at.desc()).limit(50).all()
            progress_map = collect_books_progress(db, books)
            book_ids = [book.id for book in books]
            character_counts = {
                str(book_id or ""): int(count or 0)
                for book_id, count in (
                    db.query(character_model.book_id, func.count(func.distinct(character_model.char_map_id)))
                    .filter(character_model.book_id.in_(book_ids))
                    .group_by(character_model.book_id)
                    .all()
                    if book_ids
                    else []
                )
            }
            items = []
            for book in books:
                row = serialize_book_summary(book, progress_map.get(book.id))
                row["character_count"] = int(character_counts.get(book.id, 0))
                items.append(row)
            return {"items": items}

    return {"api_books": api_books}
