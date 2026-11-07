"""`person_lines` — строки человека для `/status` и карточки `/dictor` (пакет A, 05.10).

Карточка смотрит на человека глазами админа: «ждём пробу», а не «ждём вашу пробу»; и
находит актёра, который есть только в касте, по имени (`owner_by_name`).
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, RoleDeadline, ScriptBook, ScriptChapter
from app.services import bot_status

NOW = datetime(2026, 10, 5, 9, 0, 0)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as session:
        session.add(ScriptBook(id="b1", title="Карта Приозерья", display_title="Карта Приозерья",
                               source_filename="x", source_format="docx", total_chars=0, author_sheets_x1000=0,
                               chapter_count=1, has_chapters="true", status="processing"))
        session.add(ScriptChapter(id="b1-1", book_id="b1", chapter_index=1, chapter_title="Глава 1"))
        session.add(Character(id="c1", book_id="b1", name="Убув", actor_name="Без Телеграма", appears_in="1"))
        session.add(Character(id="c2", book_id="b1", name="Сталин", actor_name="Без Телеграма?", appears_in="1"))
        session.add(RoleDeadline(character_id="c2", book_id="b1", actor_name="Без Телеграма", kind="audition",
                                 due_at=NOW + timedelta(hours=5), created_at=NOW))
        session.commit()
        yield session


def test_owner_by_name_finds_a_cast_only_actor_and_speaks_about_them(db):
    lines = bot_status.person_lines(db, bot_status.owner_by_name("Без Телеграма"), now=NOW, you=False)
    assert lines.roles == ["• «Убув» — «Карта Приозерья»: completed 0 of 1 chapter"]
    assert lines.auditions == ["• «Сталин» — «Карта Приозерья»: awaiting audition by 5 October, 17:00 MSK"]
    assert lines.rejected == []


def test_owner_by_name_ignores_other_people(db):
    lines = bot_status.person_lines(db, bot_status.owner_by_name("Иванов Пётр"), now=NOW)
    assert lines.roles == [] and lines.auditions == []
