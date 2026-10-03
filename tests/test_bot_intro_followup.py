from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.services import bot_intro

NOW = datetime(2026, 10, 1, 12, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        yield session


def test_a_new_dictor_without_roles_changes_nothing(db):
    bot_intro.create_dictor(db, "42", "Ветрова Ольга", now=NOW)
    db.commit()
    assert bot_intro.intro_followup(db, "42") == {"assignments": 0}


def _book(db, book_id, actor):
    from app.models import Character, ScriptBook
    db.add(ScriptBook(id=book_id, title=book_id, display_title=book_id, source_filename="x.docx", source_format="docx",
                      total_chars=0, author_sheets_x1000=0, chapter_count=1, has_chapters="true", status="processing"))
    db.add(Character(id=f"c-{book_id}", book_id=book_id, name="Роль", aliases="", character_color="", actor_name=actor))
    db.commit()


def test_only_books_whose_cast_matches_are_rebuilt(db, monkeypatch):
    _book(db, "mine", "Ветрова Ольга")
    _book(db, "other", "Иванов Пётр")
    bot_intro.create_dictor(db, "42", "Ветрова Ольга", now=NOW)
    db.commit()
    rebuilt = []
    monkeypatch.setattr("app.services.casting.rebuild_assignments", lambda db, book_id: rebuilt.append(book_id) or 1)
    result = bot_intro.intro_followup(db, "42")
    assert rebuilt == ["mine"] and result["assignments"] == 1
