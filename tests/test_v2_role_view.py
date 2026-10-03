"""One role's lines across the whole book, with the text around them.

A dictor works by his role, not by chapters: «покажи все реплики Бимькмолепуса».
This is that view — every line of the role in reading order, each with a couple of
paragraphs of context, chapter by chapter, paged by the actor's own lines.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment, V2StressMark
from app.v2.role_view import role_script

BOOK = "book-1"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", display_title="Крылья полумрака",
                               source_filename="k.txt", source_format="txt"))
        session.add(Character(id="c1", book_id=BOOK, name="Бимькмолепус", aliases="Бимькмолепу́с, Хальт"))
        session.add(Character(id="c2", book_id=BOOK, name="Рассказчик"))
        # three chapters, ten paragraphs each
        for chapter_index in (1, 2, 3):
            chapter = f"ch-{chapter_index}"
            session.add(ScriptChapter(id=chapter, book_id=BOOK, chapter_index=chapter_index,
                                      chapter_title=f"Глава {chapter_index}"))
            for ordinal in range(10):
                text = f"Абзац {chapter_index}.{ordinal}."
                session.add(V2Segment(id=f"{chapter}:{ordinal:05d}", book_id=BOOK, chapter_id=chapter,
                                      ordinal=ordinal, kind="paragraph", text=text,
                                      char_start=0, char_end=len(text)))
        session.commit()
        yield session


def _say(db, segment_id: str, speaker: str, version: int = 1) -> None:
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, span_start=0, span_end=12,
                         speaker=speaker, confidence=0.9, source="llm", version=version))


def test_the_role_gets_its_lines_with_the_paragraphs_around_them(db):
    _say(db, "ch-1:00004", "Бимькмолепус")
    db.commit()

    result = role_script(db, BOOK, "Бимькмолепус", radius=2)

    assert result["counts"]["lines"] == 1
    assert [c["chapter_index"] for c in result["chapters"]] == [1]
    shown = result["chapters"][0]["segments"]
    assert [item["ordinal"] for item in shown] == [2, 3, 4, 5, 6]
    assert [item["mine"] for item in shown] == [False, False, True, False, False]


def test_two_lines_close_together_share_their_context_instead_of_repeating_it(db):
    _say(db, "ch-1:00004", "Бимькмолепус")
    _say(db, "ch-1:00006", "Бимькмолепус")
    db.commit()

    result = role_script(db, BOOK, "Бимькмолепус", radius=2)
    shown = result["chapters"][0]["segments"]

    assert [item["ordinal"] for item in shown] == [2, 3, 4, 5, 6, 7, 8]
    assert sum(1 for item in shown if item["mine"]) == 2


def test_a_far_apart_line_starts_a_new_island_in_the_same_chapter(db):
    _say(db, "ch-1:00000", "Бимькмолепус")
    _say(db, "ch-1:00009", "Бимькмолепус")
    db.commit()

    shown = role_script(db, BOOK, "Бимькмолепус", radius=1)["chapters"][0]["segments"]

    assert [item["ordinal"] for item in shown] == [0, 1, 8, 9]
    assert [item["after_gap"] for item in shown] == [False, False, True, False]


def test_an_alias_and_a_stress_mark_are_the_same_role(db):
    _say(db, "ch-1:00001", "Бимькмолепу́с")
    _say(db, "ch-2:00001", "Хальт")
    db.commit()

    result = role_script(db, BOOK, "Бимькмолепус", radius=0)

    assert result["counts"] == {"lines": 2, "chapters": 2, "shown": 2, "offset": 0}


def test_only_the_newest_version_of_an_attribution_counts(db):
    _say(db, "ch-1:00003", "Бимькмолепус")
    _say(db, "ch-1:00003", "Рассказчик", version=2)
    db.commit()

    assert role_script(db, BOOK, "Бимькмолепус")["counts"]["lines"] == 0


def test_paging_counts_the_actors_own_lines_not_the_context(db):
    for ordinal in (0, 2, 4, 6, 8):
        _say(db, f"ch-1:{ordinal:05d}", "Бимькмолепус")
    db.commit()

    first = role_script(db, BOOK, "Бимькмолепус", radius=1, limit=2)
    assert first["counts"]["lines"] == 5 and first["counts"]["shown"] == 2 and first["has_more"] is True
    assert sum(1 for item in first["chapters"][0]["segments"] if item["mine"]) == 2

    last = role_script(db, BOOK, "Бимькмолепус", radius=1, limit=2, offset=4)
    assert last["has_more"] is False
    assert sum(1 for item in last["chapters"][0]["segments"] if item["mine"]) == 1


def test_the_narrator_is_a_role_like_any_other(db):
    _say(db, "ch-3:00005", "Рассказчик")
    db.commit()

    result = role_script(db, BOOK, "Рассказчик", radius=1)

    assert result["counts"]["lines"] == 1
    assert result["chapters"][0]["chapter_index"] == 3


def test_stress_marks_travel_with_the_paragraphs(db):
    _say(db, "ch-1:00002", "Бимькмолепус")
    db.add(V2StressMark(id="s1", segment_id="ch-1:00002", word_start=0, word_end=5, vowel_offset=1, version=1))
    db.commit()

    shown = role_script(db, BOOK, "Бимькмолепус", radius=0)["chapters"][0]["segments"]

    assert shown[0]["stress"] == [{"start": 0, "end": 5, "vowel": 1, "source": "dict", "rare": False}]


def test_an_unknown_book_is_none(db):
    assert role_script(db, "nope", "Бимькмолепус") is None


def test_a_dictor_does_not_get_lines_from_unpublished_chapters(db):
    """Диктор работает только по опубликованным главам: неопубликованная ещё
    правится автором, и реплика из неё может исчезнуть или сменить роль.

    До этой проверки сбор реплик отдавал главу любому, кто вошёл в систему, —
    интерфейс диктора туда не пускал, но сама ручка отвечала."""
    from app.models import ScriptChapter

    _say(db, "ch-1:00004", "Бимькмолепус")
    _say(db, "ch-2:00004", "Бимькмолепус")
    # Фикстура заводит главы со статусом по умолчанию, поэтому опубликованной делаем
    # первую явно, а вторую оставляем в работе у автора.
    db.query(ScriptChapter).filter(ScriptChapter.id == "ch-1").one().status = "published"
    db.query(ScriptChapter).filter(ScriptChapter.id == "ch-2").one().status = "final"
    db.commit()

    everything = role_script(db, BOOK, "Бимькмолепус")
    published = role_script(db, BOOK, "Бимькмолепус", published_only=True)

    seen_all = {item["chapter_id"] for item in everything["chapters"]}
    seen_pub = {item["chapter_id"] for item in published["chapters"]}
    assert "ch-2" in seen_all, "фикстура не даёт реплик во второй главе — тест бессмыслен"
    assert "ch-2" not in seen_pub
    assert seen_pub == seen_all - {"ch-2"}


def test_the_chapter_list_covers_the_whole_book_not_just_the_page(db):
    """Актёр просил сводку «в каких главах мой персонаж». Реплики приходят страницами,
    и у большой роли первая страница кончается задолго до конца книги — поэтому список
    глав считается по всем репликам роли, а не по показанным."""
    _say(db, "ch-1:00001", "Бимькмолепус")
    _say(db, "ch-1:00005", "Бимькмолепус")
    _say(db, "ch-3:00002", "Хальт")
    db.commit()

    result = role_script(db, BOOK, "Бимькмолепус", limit=1)

    assert [c["chapter_id"] for c in result["chapters"]] == ["ch-1"], "страница должна быть неполной"
    assert result["in_chapters"] == [
        {"chapter_id": "ch-1", "chapter_index": 1, "lines": 2},
        {"chapter_id": "ch-3", "chapter_index": 3, "lines": 1},
    ]


def test_a_role_without_lines_is_in_no_chapters(db):
    assert role_script(db, BOOK, "Бимькмолепус")["in_chapters"] == []
