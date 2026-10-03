"""Реплика — это абзац, где роль говорит, а не кусок разметки.

«— Сторож Линт спорит сам с собой…, — лениво заметил Пупип. — Сторож Мирон принимает
гостя…» — одна реплика, разорванная словами автора, и в разметке это два отрезка. Таблица
каста считала отрезки (Пупип — 275), а страница «Все реплики роли» — абзацы (184), и обе
подписывали число «реплик». Счёт один на всё приложение: таблица, карта персонажей,
легенда главы, выбор роли в записи, сдача глав, смета. Деньги идут по словам всех
отрезков — от способа счёта реплик они не зависят.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment

BOOK = "book-lines"
CHAPTER = "ch-1"
# one replica torn by the author's words, then a second, whole one
TORN = "— Сторож Линт спорит сам с собой, — лениво заметил Пупип. — Сторож Мирон встречает гостя."
WHOLE = "— Кыш, проказники!"


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
        session.add(Character(id="c-pupip", book_id=BOOK, name="Пупип"))
        session.add(Character(id="c-narr", book_id=BOOK, name="Рассказчик"))
        session.add(ScriptChapter(id=CHAPTER, book_id=BOOK, chapter_index=1, chapter_title="Глава 1", status="published"))
        for ordinal, text in enumerate((TORN, WHOLE)):
            session.add(V2Segment(id=f"s{ordinal}", book_id=BOOK, chapter_id=CHAPTER, ordinal=ordinal,
                                  kind="paragraph", text=text, char_start=0, char_end=len(text)))
        cut1, cut2 = TORN.index(", — лениво"), TORN.index(" — Сторож Мирон")
        for segment_id, start, end, speaker in (
            ("s0", 0, cut1, "Пупип"), ("s0", cut1, cut2, "Рассказчик"), ("s0", cut2, len(TORN), "Пупип"),
            ("s1", 0, len(WHOLE), "Пупип"),
        ):
            session.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, span_start=start, span_end=end,
                                      speaker=speaker, confidence=0.9, source="llm", version=1))
        session.commit()
        yield session


def test_the_cast_table_counts_paragraphs(db):
    from app.v2.cast_ops import book_cast

    row = next(r for r in book_cast(db, BOOK) if r["name"] == "Пупип")
    assert row["lines_count"] == 2


def test_the_character_map_counts_paragraphs(db):
    from app.v2.character_map import character_map

    row = next(r for r in character_map(db, BOOK) if r["name"] == "Пупип")
    assert row["lines"] == 2


def test_the_chapter_role_counts_count_paragraphs(db):
    from app.services.chapter_delivery import book_role_counts_by_chapter
    from app.v2.cast_ops import chapter_role_counts

    assert chapter_role_counts(db, CHAPTER)["Пупип"] == 2
    assert book_role_counts_by_chapter(db, BOOK)[CHAPTER]["Пупип"] == 2


def test_the_chapter_legend_counts_paragraphs(db):
    from app.v2.reader import build_chapter_payload

    legend = {entry["name"]: entry["lines"] for entry in build_chapter_payload(db, CHAPTER)["cast"]}
    assert legend["Пупип"] == 2


def test_the_role_page_agrees(db):
    from app.v2.role_view import role_script

    assert role_script(db, BOOK, "Пупип")["counts"]["lines"] == 2


def test_the_budget_counts_paragraphs_but_prices_every_word(db):
    from app.v2.budget_ops import aggregate_v2_lines

    pupip = aggregate_v2_lines(db, BOOK)["c-pupip"]
    assert pupip["lines"] == 2
    spoken = "— Сторож Линт спорит сам с собой — Сторож Мирон встречает гостя. — Кыш, проказники!"
    assert pupip["words"] == len(spoken.split()), "both halves of the torn replica are voiced and paid"
