"""«Слова-ловушки»: редкие слова из реплик одной роли, глава за главой, с ударением.

Рассказчик «Полумракских баек» знал, что сомнамбула — сомна́мбула; он читал в потоке и
не посмотрел. Список до записи — чтобы посмотреть заранее: только слова этой роли,
только редкие (частые он и так скажет верно), «ё» не в счёт — там не ошибиться.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Character, ScriptBook, ScriptChapter
from app.v2.models import V2Attribution, V2Segment, V2StressMark
from app.v2.role_view import role_traps

BOOK = "book-traps"
TEXTS = {
    1: ["— Сомнамбула вернулась, — сказал Пупип.", "Карлица молчала."],
    2: ["— Опять сомнамбула и факелов нет, — сказал Пупип.", "Всё зелёное вокруг."],
}


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
        session.add(Character(id="c1", book_id=BOOK, name="Пупип"))
        for index, paragraphs in TEXTS.items():
            chapter = f"ch-{index}"
            session.add(ScriptChapter(id=chapter, book_id=BOOK, chapter_index=index, chapter_title=f"Глава {index}",
                                      status="published"))
            for ordinal, text in enumerate(paragraphs):
                session.add(V2Segment(id=f"{chapter}:{ordinal}", book_id=BOOK, chapter_id=chapter, ordinal=ordinal,
                                      kind="paragraph", text=text, char_start=0, char_end=len(text)))
        session.commit()
        yield session


def _say(db, segment_id, start, end, speaker):
    db.add(V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, span_start=start, span_end=end,
                         speaker=speaker, confidence=0.9, source="llm", version=1))


def _stress(db, segment_id, word, text, vowel, source="dict"):
    start = text.index(word)
    db.add(V2StressMark(id=str(uuid.uuid4()), segment_id=segment_id, word_start=start, word_end=start + len(word),
                        vowel_offset=vowel, source=source, version=1))


@pytest.fixture()
def marked(db):
    one, two = TEXTS[1][0], TEXTS[2][0]
    # the replica is the dash-quoted part; the author's words are the narrator's
    _say(db, "ch-1:0", 0, one.index(","), "Пупип")
    _say(db, "ch-1:0", one.index(","), len(one), "Рассказчик")
    _say(db, "ch-1:1", 0, len(TEXTS[1][1]), "Рассказчик")
    _say(db, "ch-2:0", 0, two.index(","), "Пупип")
    _say(db, "ch-2:1", 0, len(TEXTS[2][1]), "Рассказчик")
    _stress(db, "ch-1:0", "Сомнамбула", one, 4)
    _stress(db, "ch-1:1", "Карлица", TEXTS[1][1], 1, source="context")
    _stress(db, "ch-2:0", "сомнамбула", two, 4)
    db.commit()
    return db


def _words(result, index):
    chapter = next(c for c in result["chapters"] if c["chapter_index"] == index)
    return [(w["word"], w["stressed"]) for w in chapter["words"]]


def test_only_the_roles_own_rare_words_with_their_stress(marked):
    result = role_traps(marked, BOOK, "Пупип", fresh=False)
    assert _words(result, 1) == [("сомнамбула", "сомна́мбула")]
    # «опять», «сказал» are everyday words; «факелов» has no mark yet — shown, but unstressed
    assert _words(result, 2) == [("сомнамбула", "сомна́мбула"), ("факелов", None)]


def test_fresh_keeps_a_word_only_where_the_role_meets_it_first(marked):
    result = role_traps(marked, BOOK, "Пупип", fresh=True)
    assert _words(result, 1) == [("сомнамбула", "сомна́мбула")]
    assert _words(result, 2) == [("факелов", None)]


def test_the_narrator_gets_the_authors_words_and_yo_words_are_left_out(marked):
    result = role_traps(marked, BOOK, "Рассказчик", fresh=False)
    # «— сказал Пупип» is the narrator's: the name is rare, and nobody has marked it yet
    assert _words(result, 1) == [("пупип", None), ("карлица", "ка́рлица")]
    assert all(c["chapter_index"] != 2 for c in result["chapters"]), "«Всё зелёное» — «ё», нечего проверять"


def test_a_dictor_sees_only_published_chapters(marked):
    marked.query(ScriptChapter).filter(ScriptChapter.id == "ch-2").one().status = "final"
    marked.commit()
    result = role_traps(marked, BOOK, "Пупип", fresh=False, published_only=True)
    assert [c["chapter_index"] for c in result["chapters"]] == [1]


def test_an_unknown_book_is_none(db):
    assert role_traps(db, "nope", "Пупип") is None


def test_the_whole_book_path_reads_the_same_marks(marked, monkeypatch):
    """The narrator is most of the book: his marks come from one join, not by id — same answer."""
    import app.v2.role_view as role_view

    by_id = role_traps(marked, BOOK, "Рассказчик", fresh=False)
    monkeypatch.setattr(role_view, "_WHOLE_BOOK_SEGMENTS", 0)
    whole = role_traps(marked, BOOK, "Рассказчик", fresh=False)
    assert whole == by_id
    assert _words(whole, 1) == [("пупип", None), ("карлица", "ка́рлица")]


def test_a_drawn_out_word_is_not_a_word(db):
    """«Коне-е-ечно!» splits into «коне», «е», «ечно» — a stretch of the voice, not rare words."""
    text = "— Коне-е-ечно, сомнамбула!"
    db.add(V2Segment(id="ch-1:9", book_id=BOOK, chapter_id="ch-1", ordinal=9, kind="paragraph", text=text,
                     char_start=0, char_end=len(text)))
    _say(db, "ch-1:9", 0, len(text), "Пупип")
    db.commit()
    assert _words(role_traps(db, BOOK, "Пупип", fresh=False), 1) == [("сомнамбула", None)]
