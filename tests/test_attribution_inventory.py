"""Опись книги по корзинам доказательств."""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.time_utils import utcnow_naive
from app.v2.models import V2Attribution, V2Segment


@pytest.fixture()
def book():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        db.add(ScriptBook(id="b1", title="Крылья полумрака", source_filename="k.txt",
                          source_format="txt", created_at=utcnow_naive()))
        db.add(ScriptChapter(id="ch1", book_id="b1", chapter_index=1,
                             chapter_title="Глава 1", status="published"))
        db.add(Character(id="c1", book_id="b1", name="Дгарнин", aliases="",
                         appears_in="1", created_at=utcnow_naive()))
        db.add(Character(id="c2", book_id="b1", name="Едвбабак", aliases="",
                         appears_in="60", created_at=utcnow_naive()))
        # Первый абзац лежит ЦЕЛЬНЫМ отрезком — так разметка выглядела до того, как
        # речь и ремарку разделили. Разделение приходит второй версией в тесте на
        # ловушку версий ниже.
        paragraphs = [
            (0, "- Нас стало слишком много, - сказал Дгарнин.",
             [("Дгарнин", 0, 44)]),
            (1, "- Из числа одаренных, - добавил незнакомец.",
             [("Едвбабак", 0, 22), ("Рассказчик", 22, 43)]),
        ]
        for ordinal, text, spans in paragraphs:
            segment_id = f"ch1:{ordinal:05d}"
            db.add(V2Segment(id=segment_id, book_id="b1", chapter_id="ch1",
                             ordinal=ordinal, kind="paragraph", text=text))
            for speaker, start, end in spans:
                db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id,
                                     version=1, speaker=speaker, span_start=start,
                                     span_end=end, source="llm", created_at=utcnow_naive()))
        db.commit()
        yield db


def test_the_inventory_counts_every_replica_once(book):
    from app.pipeline.attribution_inventory import build_inventory

    out = build_inventory(book, book_id="b1")

    assert sum(out["totals"].values()) == 2
    assert out["totals"]["cue_named"] == 1
    assert out["totals"]["unnamed_speaker"] == 1


def test_a_character_speaking_outside_its_cast_chapters_is_reported(book):
    from app.pipeline.attribution_inventory import build_inventory

    out = build_inventory(book, book_id="b1")

    outside = out["cross_checks"]["outside_appears_in"]
    assert [row["speaker"] for row in outside] == ["Едвбабак"]
    assert outside[0]["chapter_index"] == 1


def test_only_the_effective_version_of_a_paragraph_is_counted(book):
    """Ловушка версий: старая цельная реплика не должна попасть в опись рядом с новой.

    Так ловушка выглядит в жизни: версия 1 — один отрезок на весь абзац, версия 2 —
    тот же абзац, разделённый на речь и ремарку. Координаты у версий РАЗНЫЕ, и
    читалка, отдающая все версии подряд, насчитает здесь две реплики вместо одной.
    Записать обе версии с одинаковыми границами — значит проверить не ловушку, а
    схлопывание дублей по ключу.
    """
    from app.pipeline.attribution_inventory import build_inventory

    book.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch1:00000", version=2,
                           speaker="Дгарнин", span_start=0, span_end=27, source="llm",
                           created_at=utcnow_naive()))
    book.add(V2Attribution(id=str(uuid.uuid4()), segment_id="ch1:00000", version=2,
                           speaker="Рассказчик", span_start=27, span_end=44, source="llm",
                           created_at=utcnow_naive()))
    book.commit()

    out = build_inventory(book, book_id="b1")

    assert sum(out["totals"].values()) == 2


def test_every_proven_line_carries_its_quote(book):
    """Доказательство, которого не видно в описи, проверить нечем — значит, его нет."""
    from app.pipeline.attribution_inventory import build_inventory, render_inventory

    inventory = build_inventory(book, book_id="b1")

    proven = inventory["proven"]
    assert [row["kind"] for row in proven] == ["cue_named"]
    assert "сказал Дгарнин" in proven[0]["quote"]
    assert proven[0]["evidence_paragraph"] == 0
    assert "сказал Дгарнин" in render_inventory(inventory)


def test_two_lines_in_a_row_by_one_character_reach_the_report(book):
    """Четвёртая перекрёстная проверка спеки доезжает до отчёта, а не только до кода."""
    from app.pipeline.attribution_inventory import build_inventory

    book.add(ScriptChapter(id="ch2", book_id="b1", chapter_index=2,
                           chapter_title="Глава 2", status="published"))
    for ordinal, text, speaker in [(0, "- Раз.", "Дгарнин"), (1, "- Два.", "Едвбабак"),
                                   (2, "- Три.", "Едвбабак")]:
        segment_id = f"ch2:{ordinal:05d}"
        book.add(V2Segment(id=segment_id, book_id="b1", chapter_id="ch2",
                           ordinal=ordinal, kind="paragraph", text=text))
        book.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, version=1,
                               speaker=speaker, span_start=0, span_end=len(text),
                               source="llm", created_at=utcnow_naive()))
    book.commit()

    found = build_inventory(book, book_id="b1")["cross_checks"]["same_speaker_twice"]

    assert found == [{"chapter_index": 2, "speaker": "Едвбабак", "paragraph": 2,
                      "previous_paragraph": 1}]


def test_the_summary_names_the_doubtful_bucket(book):
    from app.pipeline.attribution_inventory import build_inventory, render_inventory

    text = render_inventory(build_inventory(book, book_id="b1"))

    assert "доказано текстом" in text
    assert "под вопросом" in text
    assert "Едвбабак" in text
