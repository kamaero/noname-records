"""The legend editor's cast: every Character row of the book, counted from effective v2 spans."""
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import BookBudget, Character, ScriptBook, ScriptChapter
from app.v2.cast_ops import approved_actor, as_tentative, book_cast, cast_canonicalizer, is_placeholder_name, parse_actor_name, split_aliases
from app.v2.models import V2Attribution, V2Segment
from app.v2.reader import NARRATOR

BOOK = "book-1"
CH1, CH2 = "ch-1", "ch-2"


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _attr(segment_id, speaker, version=1, start=0, end=10):
    return V2Attribution(id=str(uuid.uuid4()), segment_id=segment_id, span_start=start, span_end=end,
                         speaker=speaker, confidence=0.9, source="llm", version=version)


def _seed(db, *, with_narrator_row=False):
    db.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
    db.add(ScriptChapter(id=CH1, book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
    db.add(ScriptChapter(id=CH2, book_id=BOOK, chapter_index=2, chapter_title="Глава 2"))
    db.add(Character(id="c1", book_id=BOOK, char_map_id="m1", name="Дгарнин", aliases="Пресвитер, Дгарнин Кислятина",
                     actor_name="Иван Петров", character_color="#112233", character_text_color="#FFFFFF",
                     character_font_weight="700", character_font_style="normal"))
    db.add(Character(id="c2", book_id=BOOK, char_map_id="m2", name="Пупип", actor_name=""))
    db.add(Character(id="c3", book_id=BOOK, char_map_id="m3", name="Молчун", actor_name="Никто"))
    db.add(Character(id="c9", book_id=BOOK, char_map_id="m9", name="UNSURE:кто-то", actor_name=""))
    if with_narrator_row:
        db.add(Character(id="cn", book_id=BOOK, char_map_id="mn", name=NARRATOR, actor_name="Голос Книги"))
    db.add(BookBudget(id="b1", book_id=BOOK, narrator_actor_name="Голос Автора"))
    for i, chapter in enumerate((CH1, CH1, CH2)):
        seg = f"{chapter}:{i:05d}"
        db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=chapter, ordinal=i, text="…", char_end=1))
    a, b, c = f"{CH1}:00000", f"{CH1}:00001", f"{CH2}:00002"
    db.add(_attr(a, "Пупип", version=1))            # superseded by version 2 below
    db.add(_attr(a, NARRATOR, version=2))
    db.add(_attr(b, "Дгарни́н", start=0, end=5))     # stress mark in the speaker name
    db.add(_attr(b, "Пресвитер", start=5, end=10))   # an alias
    db.add(_attr(c, "Дгарнин"))
    db.add(_attr(c, "UNSURE", start=0, end=3))
    db.add(_attr(c, "Незнакомец", start=3, end=6))   # not in the cast
    db.commit()


def test_placeholders_and_aliases_helpers():
    assert is_placeholder_name("UNSURE") and is_placeholder_name("unsure: голос") and not is_placeholder_name("Пупип")
    assert split_aliases("Пресвитер, Дгарнин Кислятина;Папа") == ["Пресвитер", "Дгарнин Кислятина", "Папа"]
    assert split_aliases('["Жрец", "Угодник"]') == ["Жрец", "Угодник"]
    assert split_aliases("") == []


def test_parse_actor_name_strips_the_trailing_question_mark():
    assert parse_actor_name("Натали Ким?") == ("Натали Ким", True)
    assert parse_actor_name("Натали Ким") == ("Натали Ким", False)


def test_parse_actor_name_treats_bare_question_marks_as_undecided():
    """«???» без имени — живая пометка «ещё не решил», назначать предварительно
    некого, роль свободна."""
    assert parse_actor_name("???") == ("", False)
    assert parse_actor_name("") == ("", False)


def test_cast_canonicalizer_folds_case_and_aliases_to_the_cast_spelling():
    rows = [Character(book_id=BOOK, name="Гамук", aliases="Гамукушка")]
    canonicalize = cast_canonicalizer(rows)
    assert canonicalize("ГАМУК") == "Гамук"
    assert canonicalize("гамукушка") == "Гамук"
    assert canonicalize("Кто-то ещё") == "Кто-то ещё", "не свернулось никуда — остаётся собственной строкой"


def test_book_cast_counts_lines_and_chapters_from_effective_attributions():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        rows = book_cast(db, BOOK)

    assert [r["name"] for r in rows] == [NARRATOR, "Дгарнин", "Молчун", "Пупип"]
    narrator, dgarnin, silent, pupip = rows
    assert narrator["is_narrator"] is True and narrator["character_id"] == "" and narrator["char_map_id"] == ""
    assert narrator["actor_name"] == "Голос Автора"
    assert narrator["lines_count"] == 1 and narrator["appears_in_chapters"] == [1]

    assert dgarnin["character_id"] == "c1" and dgarnin["char_map_id"] == "m1" and dgarnin["is_narrator"] is False
    # «Дгарни́н» and «Пресвитер» share one paragraph: one replica torn in two, not two
    assert dgarnin["lines_count"] == 2, "a paragraph the role speaks in counts once, whatever it is called there"
    assert dgarnin["appears_in_chapters"] == [1, 2]
    assert dgarnin["actor_name"] == "Иван Петров"
    # A chosen background is kept; the text colour is re-derived for contrast, as the reader does.
    assert dgarnin["character_color"] == "#112233" and dgarnin["character_text_color"].startswith("#")
    assert dgarnin["character_font_weight"] and dgarnin["character_font_style"] in ("normal", "italic")

    # Version 1 said Пупип for the first segment; version 2 overruled it, so he has no lines.
    assert pupip["lines_count"] == 0 and pupip["appears_in_chapters"] == []
    assert pupip["character_color"].startswith("#") and pupip["character_text_color"].startswith("#")
    assert silent["lines_count"] == 0
    assert not any(r["name"].startswith("UNSURE") for r in rows)


def test_an_alias_or_a_stressed_name_alone_still_counts_for_the_role():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        for i, speaker in ((3, "Пресвитер"), (4, "Дгарни́н")):
            seg = f"{CH2}:{i:05d}"
            db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=CH2, ordinal=i, text="…", char_end=1))
            db.add(_attr(seg, speaker))
        db.commit()
        dgarnin = next(r for r in book_cast(db, BOOK) if r["name"] == "Дгарнин")
    assert dgarnin["lines_count"] == 4


def test_only_speaking_drops_silent_characters_but_keeps_the_narrator():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        assert [r["name"] for r in book_cast(db, BOOK, only_speaking=True)] == [NARRATOR, "Дгарнин"]


def test_a_narrator_character_row_becomes_the_narrator_entry():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db, with_narrator_row=True)
        rows = book_cast(db, BOOK)
    assert rows[0]["name"] == NARRATOR and rows[0]["character_id"] == "cn" and rows[0]["char_map_id"] == "mn"
    assert rows[0]["actor_name"] == "Голос Книги" and rows[0]["lines_count"] == 1
    assert [r["name"] for r in rows].count(NARRATOR) == 1


def test_book_without_characters_or_segments_still_has_a_narrator():
    SessionLocal = _session()
    with SessionLocal() as db:
        db.add(ScriptBook(id=BOOK, title="Пусто", source_filename="k.txt", source_format="txt"))
        db.commit()
        rows = book_cast(db, BOOK)
    assert len(rows) == 1 and rows[0]["is_narrator"] and rows[0]["lines_count"] == 0


class TestTheAgentsQuestionMark:
    """Имя от агента всегда предварительное — даже когда он забыл поставить знак."""

    def test_a_bare_name_gets_the_question_mark(self):
        assert as_tentative("Натали Ким") == "Натали Ким?"

    def test_a_name_that_already_has_one_is_left_alone(self):
        assert as_tentative("Натали Ким?") == "Натали Ким?"

    def test_an_empty_name_stays_empty(self):
        """Пустое имя — снятие собственного предложения, а не назначение никого."""
        assert as_tentative("") == ""

    def test_whitespace_is_not_a_name(self):
        assert as_tentative("   ") == "   "

    def test_the_undecided_mark_is_not_a_name_either(self):
        """«???» — пометка «ещё не решил»: назначать предварительно некого."""
        assert as_tentative("???") == "???"


class TestWhoCountsAsCastAtAll:
    """`approved_actor` — единственная дверь, через которую предварительное имя не проходит.

    `names_match` знака вопроса не видит: он чистит строку до букв и цифр, и «Натали
    Ким?» для него та же Натали Ким. Всем, кто спрашивает «этот актёр утверждён?»,
    отвечает этот помощник, а не собственная проверка на хвостовой «?»: нотация
    разбирается в одном месте, иначе один и тот же каст читается по-разному.
    """

    def test_an_approved_name_comes_back_whole(self):
        assert approved_actor("Натали Ким") == "Натали Ким"

    def test_a_tentative_name_is_nobody(self):
        assert approved_actor("Натали Ким?") == ""

    def test_an_empty_cast_entry_is_nobody(self):
        assert approved_actor("") == ""
        assert approved_actor("   ") == ""

    def test_the_undecided_mark_is_nobody(self):
        assert approved_actor("???") == ""

    def test_the_name_comes_back_trimmed(self):
        assert approved_actor("  Натали Ким  ") == "Натали Ким"

    def test_a_tentative_actor_stays_nobody_even_with_the_audition_invite(self):
        """Приглашение на пробу (`app/services/role_approval.py`) не меняет эту дверь:
        запись страницы записи по-прежнему считает загрузку предварительного актёра
        пробой, а не ролью целиком, — этот помощник для неё пуст, как и раньше."""
        assert approved_actor("Гульнара Ткач?") == ""
