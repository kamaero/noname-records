"""Главы роли — по текущей разметке, а не по графе `appears_in`.

29.10 рассылка «дубли можно слать в бот» назвала Привалову главы 15 и 39 Хдыбдяра,
где его реплик давно нет, и не назвала другим дикторам главы, куда реплики
переехали: `appears_in` заполняет извлечение персонажей, и за правками разметки она
не следует. По той же графе считались сроки, «сдано X из Y» и главы, которые бот
ищет в присланной «колбасе».
"""
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AudioFile, Character, ScriptBook, ScriptChapter
from app.services import role_deadlines
from app.services.audio_uploads import derive_book_code
from app.services.bot_studio import Studio
from app.v2.models import V2Attribution, V2Segment

BOOK = "b1"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Карта", display_title="Карта", source_filename="k.txt",
                               source_format="txt"))
        session.add(Character(id="c1", book_id=BOOK, name="Хдыбдяр", aliases="Дзимве́л", appears_in="1,2",
                              actor_name="Примерлаяюмнфшв Примерцосезбсдв"))
        session.add(Character(id="c2", book_id=BOOK, name="Рассказчик"))
        for index in (1, 2, 3):
            chapter = f"ch-{index}"
            session.add(ScriptChapter(id=chapter, book_id=BOOK, chapter_index=index, chapter_title=f"Глава {index}"))
            for ordinal in range(3):
                text = f"Абзац {index}.{ordinal}."
                session.add(V2Segment(id=f"{chapter}:{ordinal:05d}", book_id=BOOK, chapter_id=chapter,
                                      ordinal=ordinal, kind="paragraph", text=text,
                                      char_start=0, char_end=len(text)))
        session.commit()
        yield session


def _say(db, segment_id: str, speaker: str, version: int = 1) -> None:
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, span_start=0, span_end=10,
                         speaker=speaker, confidence=0.9, source="llm", version=version))
    db.commit()


def _fresh(db):
    """Новая сессия глазами кэша: карта глав живёт в `db.info` до конца сессии."""
    db.info.clear()
    return db


def test_the_chapters_of_a_role_follow_the_markup_not_appears_in(db):
    _say(db, "ch-2:00001", "Хдыбдяр")
    _say(db, "ch-3:00000", "Дзимве́л")  # псевдоним с ударением — та же роль

    chapters = role_deadlines.role_chapters(_fresh(db), db.get(Character, "c1"))

    assert sorted(chapters) == [2, 3]


def test_a_line_moved_to_another_role_leaves_the_chapter(db):
    _say(db, "ch-2:00001", "Хдыбдяр")
    _say(db, "ch-3:00000", "Хдыбдяр")
    _say(db, "ch-2:00001", "Рассказчик", version=2)

    assert sorted(role_deadlines.role_chapters(_fresh(db), db.get(Character, "c1"))) == [3]


def test_a_role_with_no_lines_in_the_markup_has_no_chapters(db):
    _say(db, "ch-1:00000", "Рассказчик")

    assert role_deadlines.role_chapters(_fresh(db), db.get(Character, "c1")) == {}


def test_a_book_without_markup_falls_back_to_appears_in(db):
    db.add(ScriptBook(id="b2", title="Старая", display_title="Старая", source_filename="s.txt", source_format="txt"))
    db.add(ScriptChapter(id="old-1", book_id="b2", chapter_index=1, chapter_title="Глава 1"))
    db.add(Character(id="c9", book_id="b2", name="Ао", appears_in="1"))
    db.commit()

    assert sorted(role_deadlines.role_chapters(_fresh(db), db.get(Character, "c9"))) == [1]


def test_progress_counts_the_markup_chapters(db):
    _say(db, "ch-3:00000", "Хдыбдяр")
    db.add(AudioFile(book_code=derive_book_code("Карта"), original_filename="t.wav", stored_key="k/3",
                     mime_type="audio/wav", size_bytes=1, chapter="Глава 3", role="Хдыбдяр",
                     actor_name="Примерлаяюмнфшв Примерцосезбсдв", kind="take", canonical_filename="t3.wav"))
    db.commit()

    assert role_deadlines.role_progress(_fresh(db), db.get(Character, "c1"), "Примерлаяюмнфшв Примерцосезбсдв") == (1, 1)


def test_the_studio_snapshot_counts_the_markup_chapters_for_a_role_given_by_name(db):
    # «Смета» передаёт не строку каста, а имя роли с книгой и графой appears_in
    _say(db, "ch-3:00000", "Хдыбдяр")
    studio = Studio(_fresh(db), now=datetime(2026, 10, 29))

    role = SimpleNamespace(book_id=BOOK, name="Хдыбдяр", appears_in="1,2")
    assert studio.progress(role, "Примерлаяюмнфшв Примерцосезбсдв") == (0, 1)


def test_a_role_whose_lines_all_moved_away_closes_its_deadline(db):
    from app.models import RoleDeadline

    _say(db, "ch-1:00000", "Рассказчик")  # у книги есть разметка, у Хдыбдяра — ни реплики
    db.add(RoleDeadline(character_id="c1", book_id=BOOK, actor_name="Примерлаяюмнфшв Примерцосезбсдв", kind="role",
                        due_at=datetime(2026, 11, 18), created_at=datetime(2026, 10, 1)))
    db.commit()
    sent = []

    role_deadlines.run_pass(_fresh(db), now=datetime(2026, 11, 19), send=lambda chat, text: sent.append(text) or True)

    row = db.query(RoleDeadline).one()
    assert row.close_reason == "done"
    assert sent == []


def test_a_book_without_markup_keeps_a_role_without_chapters_open(db):
    from app.models import RoleDeadline

    db.add(ScriptBook(id="b2", title="Старая", display_title="Старая", source_filename="s.txt", source_format="txt"))
    db.add(Character(id="c9", book_id="b2", name="Ао", appears_in="", actor_name="Примернхкэятжщб Примерезчзжовув"))
    db.add(RoleDeadline(character_id="c9", book_id="b2", actor_name="Примернхкэятжщб Примерезчзжовув", kind="role",
                        due_at=datetime(2026, 11, 18), created_at=datetime(2026, 10, 1)))
    db.commit()

    role_deadlines.run_pass(_fresh(db), now=datetime(2026, 11, 1), send=lambda chat, text: True)

    assert db.query(RoleDeadline).filter(RoleDeadline.character_id == "c9").one().closed_at is None
