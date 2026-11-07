"""Сценарий роли наружу: реплики с подводкой, ударения в редких словах, имя файла записи."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.v2.role_export as role_export
from app.db import Base
from app.v2.role_export import build_role_export, content_disposition, download_names, render_text
from tests.role_export_book import BOOK, build_book


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        build_book(session)
        yield session


def _texts(line):
    return [(piece.text, piece.own) for piece in line.pieces]


def test_the_header_says_who_the_role_is_and_who_reads_it(db):
    export = build_role_export(db, BOOK, "Мира")

    assert (export.book_title, export.role, export.actor) == ("Тихая гавань", "Мира", "Анна Тестова")
    assert export.facts == "человек · вспыльчивая" and export.note == "говорит быстро"
    assert export.line_count == 3 and [c.index for c in export.chapters] == [3]


def test_each_chapter_names_the_file_the_actor_should_send(db):
    chapter = build_role_export(db, BOOK, "Мира").chapters[0]

    assert chapter.title == "Глава 3. Тайна"
    assert chapter.filename == "TG_Ch03_Mira_AnnaTestova.wav"


def test_a_tentative_actor_gets_no_filename_rather_than_unknown(db):
    export = build_role_export(db, BOOK, "Ярош")

    assert export.actor == "" and export.chapters[0].filename == ""


def test_the_cue_is_the_paragraph_before_unless_it_is_the_roles_own(db):
    lines = build_role_export(db, BOOK, "Мира").chapters[0].lines

    assert [(line.cue_speaker, line.cue_text) for line in lines] == [
        ("Рассказчик", "И тогда дверь открылась."),
        ("", ""),                       # две реплики подряд — подводка не нужна
        ("Ярош", "— Ты опоздала."),
    ]


def test_the_authors_words_inside_a_line_are_not_the_actors(db):
    first = build_role_export(db, BOOK, "Мира").chapters[0].lines[0]

    assert _texts(first) == [("— Я не знала,", True), ("— сказала она,", False), ("— что ты здесь.", True)]


def test_only_rare_words_and_human_marks_are_stressed(db):
    second = build_role_export(db, BOOK, "Мира").chapters[0].lines[1]

    assert _texts(second) == [("— Прости\u0301, карли\u0301ца задержала.", True)]


def test_the_narrator_reads_the_remark_inside_somebody_elses_line(db):
    lines = build_role_export(db, BOOK, "Рассказчик").chapters[0].lines

    assert _texts(lines[1]) == [("— Я не знала,", False), ("— сказала она,", True), ("— что ты здесь.", False)]


def test_a_page_boundary_loses_nothing_and_repeats_nothing(db, monkeypatch):
    monkeypatch.setattr(role_export, "MAX_LIMIT", 1)

    lines = build_role_export(db, BOOK, "Мира").chapters[0].lines

    assert len(lines) == 3
    # реплика 2 идёт сразу за своей же репликой с прошлой страницы — подводки нет
    assert (lines[1].cue_speaker, lines[1].cue_text) == ("", "")


def test_a_long_cue_keeps_its_end(db):
    from app.v2.models import V2Segment

    segment = db.get(V2Segment, "ch-3:00000")
    segment.text = "Слово " * 60 + "и тогда дверь открылась."
    db.commit()
    cue = build_role_export(db, BOOK, "Мира").chapters[0].lines[0].cue_text

    assert cue.startswith("…") and cue.endswith("и тогда дверь открылась.")
    assert len(cue) <= role_export.CUE_CHARS + 1


def test_unpublished_chapters_stay_out_for_a_dictor(db):
    from app.models import ScriptChapter

    db.get(ScriptChapter, "ch-3").status = "queued"
    db.commit()

    assert build_role_export(db, BOOK, "Мира", published_only=True).chapters == []
    assert build_role_export(db, BOOK, "Мира").line_count == 3


def test_an_unknown_book_is_none(db):
    assert build_role_export(db, "nope", "Мира") is None


def test_the_text_reads_like_the_screen(db):
    text = render_text(build_role_export(db, BOOK, "Мира"))

    assert text.startswith("Мира — «Тихая гавань»\nActor: Анна Тестова\n")
    assert "Director note: говорит быстро" in text
    assert "Rare words may carry pronunciation marks." in text
    assert "=== Глава 3. Тайна ===\nRecording file: TG_Ch03_Mira_AnnaTestova.wav" in text
    assert "  Narrator (system): И тогда дверь открылась.\n▶ Мира: — Я не знала, [— сказала она,] — что ты здесь." in text
    assert "▶ Мира: — Прости\u0301, карли\u0301ца задержала." in text
    assert "  Ярош: — Ты опоздала.\n▶ Мира: — Знаю." in text


def test_a_title_without_the_word_chapter_gets_it(db):
    from app.v2.role_export import Chapter, chapter_heading

    assert chapter_heading(Chapter(index=7, title="Пролог", filename="")) == "Chapter 7. Пролог"
    assert chapter_heading(Chapter(index=7, title="", filename="")) == "Chapter 7"
    assert chapter_heading(Chapter(index=7, title="Глава 7. Тайна", filename="")) == "Глава 7. Тайна"


def test_the_download_name_survives_quotes_and_yo(db):
    export = build_role_export(db, BOOK, "Мира")
    export.role, export.book_title = "Ёжик «Малый»", "Тихая гавань"

    full, ascii_name = download_names(export, "pdf")
    header = content_disposition(export, "pdf")

    assert full == "Ёжик «Малый» — «Тихая гавань».pdf"
    assert ascii_name == "TG_EzhikMalyy.pdf"
    header.encode("latin-1")  # заголовок HTTP — latin-1; кириллица только в filename*
    assert "filename*=UTF-8''" in header and 'filename="TG_EzhikMalyy.pdf"' in header


def _segment(text, *spans):
    return {"text": text, "stress": [],
            "spans": [{"start": start, "end": end, "speaker": who} for who, start, end in spans]}


def test_an_earlier_overlapping_span_keeps_its_words_as_in_the_reader():
    # Читалка (splitRuns) отдаёт перекрытие тому, кто начался раньше: актёр не должен
    # получить строкой то, что на экране принадлежит партнёру
    from app.v2.role_export import _pieces

    pieces = _pieces(_segment("Чужое своё", ("Мира", 0, 10), ("Ярош", 0, 6)), {"Мира"})

    assert [(p.text, p.own) for p in pieces] == [("Чужое", False), ("своё", True)]


def test_a_span_swallowed_by_an_earlier_one_gives_the_role_nothing():
    from app.v2.role_export import _pieces

    pieces = _pieces(_segment("Чужое своё", ("Ярош", 0, 10), ("Мира", 6, 10)), {"Мира"})

    assert [(p.text, p.own) for p in pieces] == [("Чужое своё", False)]
