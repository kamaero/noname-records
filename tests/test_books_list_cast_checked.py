"""Список книг несёт отметку «Карта проверена».

«Каст» в навбаре открывает книгу, чью карту автор проверил последней, — это книга,
над которой студия работает сейчас. Без даты в списке фронт открывал первую книгу
по дате загрузки («Сезон ветров 4»), хотя работа шла над «Картой».
"""
from datetime import datetime

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from app.api.books_list import build_books_list_handlers
from app.db import Base
from app.models import Character, ScriptBook
from app.time_utils import utcnow_naive


def test_books_carry_the_cast_checked_date():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        db.add_all([
            ScriptBook(id="kp", title="Карта", source_filename="k.txt", source_format="txt", created_at=utcnow_naive(),
                       cast_checked_at=datetime(2026, 9, 12, 11, 41, 6)),
            ScriptBook(id="sv4", title="Семья 4", source_filename="s.txt", source_format="txt", created_at=utcnow_naive()),
        ])
        db.commit()

    handlers = build_books_list_handlers({
        "is_authenticated": lambda request: True,
        "SessionLocal": factory,
        "func": func,
        "ScriptBook": ScriptBook,
        "Character": Character,
        "collect_books_progress": lambda db, books: {},
        "serialize_book_summary": lambda book, progress: {"id": book.id},
    })
    items = {item["id"]: item for item in handlers["api_books"](None)["items"]}

    assert items["kp"]["cast_checked_at"].startswith("2026-09-12T11:41:06")
    assert items["sv4"]["cast_checked_at"] == ""
