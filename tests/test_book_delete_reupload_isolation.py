from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api.book_actions import build_book_actions_handlers
from app.db import Base
from app.models import (
    BookBudget,
    Character,
    CharacterBudgetSnapshot,
    OperatorIntervention,
    ScriptBook,
    ScriptChapter,
    ScriptJob,
    ScriptLog,
)
from app.services.book_import import enqueue_book
from app.services.book_progress import collect_books_progress


def _request(method: str = "POST", path: str = "/api/books/book-1/delete") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def test_delete_book_removes_pipeline_rows_and_reupload_starts_fresh() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    payload = (
        "Глава 1\n"
        "Первая глава.\n\n"
        "Глава 2\n"
        "Вторая глава.\n\n"
        "Глава 3\n"
        "Третья глава.\n"
    ).encode("utf-8")

    with SessionLocal() as db:
        first = enqueue_book(
            db=db,
            filename="same-book.txt",
            payload=payload,
            created_by_user_id="user-1",
            created_by_name="Тест",
        )
        first_book_id = first.id

        assert first.status == "uploaded"
        assert first.pipeline_mode == "v2"
        assert db.query(ScriptJob).filter(ScriptJob.book_id == first.id).count() == 0
        assert db.query(ScriptChapter).filter(ScriptChapter.book_id == first.id).count() == 3

    handlers = build_book_actions_handlers(
        {
            "is_authenticated": lambda request: True,
            "has_workspace_full_access": lambda request: True,
            "has_any_role": lambda request, roles: "admin" in roles,
            "session_payload": lambda request: {"uid": "user-1"},
            "session_display_name": lambda request: "Тест",
            "SessionLocal": SessionLocal,
            "get_book": lambda db, book_id: db.query(ScriptBook).filter(ScriptBook.id == book_id).first(),
            "enqueue_book": enqueue_book,
            "audit": lambda *args, **kwargs: None,
            "ScriptLog": ScriptLog,
            "ScriptChapter": ScriptChapter,
            "ScriptJob": ScriptJob,
            "Character": Character,
            "CharacterBudgetSnapshot": CharacterBudgetSnapshot,
            "BookBudget": BookBudget,
            "OperatorIntervention": OperatorIntervention,
        }
    )

    response = handlers["api_delete_book"](_request(), first_book_id)
    assert response.status_code == 200

    with SessionLocal() as db:
        assert db.query(ScriptBook).filter(ScriptBook.id == first_book_id).count() == 0
        assert db.query(ScriptChapter).filter(ScriptChapter.book_id == first_book_id).count() == 0
        assert db.query(ScriptJob).filter(ScriptJob.book_id == first_book_id).count() == 0
        assert db.query(ScriptLog).filter(ScriptLog.book_id == first_book_id).count() == 0

        second = enqueue_book(
            db=db,
            filename="same-book.txt",
            payload=payload,
            created_by_user_id="user-1",
            created_by_name="Тест",
        )

        progress = collect_books_progress(db, [second])[second.id]

        assert db.query(ScriptBook).count() == 1
        assert db.query(ScriptChapter).filter(ScriptChapter.book_id == second.id).count() == 3
        assert db.query(ScriptJob).filter(ScriptJob.book_id == second.id).count() == 0
        assert second.status == "uploaded"
        assert progress["queued"] == 0
        assert progress["waiting"] == 0
