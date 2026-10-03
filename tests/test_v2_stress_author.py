"""Reading the author's own stress list.

The list is a Google Doc pasted into a .docx: one name per paragraph, the stressed
vowel written as a CAPITAL letter inside the word. It was typed by a person, so it
carries every irregularity a person produces — two forms with a slash, a note in
parentheses, a non-breaking hyphen, a trailing comma, two names in one paragraph
separated by a line break, and names whose only capital is the first letter, which
tells us nothing about stress.
"""
import docx
import pytest

from app.v2.author_stress import (
    AuthorStress,
    parse_author_notation,
    read_author_stress_docx,
    stressed_form,
)


def _words(entries):
    return [(e.word_lower, e.vowel_index) for e in entries]


def test_plain_name_stressed_capital_inside():
    assert _words(parse_author_notation("АбхилагАша")) == [("абхилагаша", 7)]


def test_capital_second_letter_is_stress_not_initial():
    # «АдАлка»: the A at index 0 is just the initial; the stress is the one at index 2.
    assert _words(parse_author_notation("АдАлка")) == [("адалка", 2)]


def test_initial_capital_only_means_unknown():
    assert parse_author_notation("Изельд") == []
    assert parse_author_notation("Угвор ") == []


def test_two_forms_separated_by_slash():
    assert _words(parse_author_notation("БушУк/бушукИ")) == [("бушук", 3), ("бушуки", 5)]


def test_multiword_entry_gives_one_entry_per_word():
    assert _words(parse_author_notation("АзЕй ДебАль")) == [("азей", 2), ("дебаль", 3)]


def test_note_in_parentheses_is_kept_and_apostrophe_splits():
    entries = parse_author_notation("ЗвИрр'КрАА (как вороний крик)")
    assert _words(entries) == [("звирр", 2), ("краа", 2)]
    assert {e.note for e in entries} == {"как вороний крик"}


def test_non_breaking_hyphen_parts_are_separate_words():
    # «Йог‑Сотхотх»: U+2011 between the parts; «Йог» has no internal capital → skipped.
    assert _words(parse_author_notation("Йог‑СотхОтх")) == [("сотхотх", 4)]


def test_trailing_comma_and_spaces_are_ignored():
    assert _words(parse_author_notation("АрборАз,")) == [("арбораз", 5)]
    assert _words(parse_author_notation("ЛиствОр  ")) == [("листвор", 5)]


def test_two_names_in_one_paragraph_split_by_line_break():
    assert _words(parse_author_notation("ТасварксезЕн\nТаштарАгис")) == [
        ("тасварксезен", 10), ("таштарагис", 6),
    ]


def test_dash_with_spaces_starts_a_note():
    entries = parse_author_notation('Сальван - слово с двойным ударением, как "творог". Можно как угодно.')
    assert entries == []  # «Сальван» has no internal capital, so nothing is known
    entries = parse_author_notation("ВОрона - примечание")
    assert _words(entries) == [("ворона", 1)]
    assert entries[0].note == "примечание"


def test_hyphen_without_spaces_is_a_word_separator():
    assert _words(parse_author_notation("Тхай -Тхий-ТхагекАш")) == [("тхагекаш", 6)]


def test_capital_consonant_is_not_a_stress():
    # «МАЙно»: А at 1 is the stress; Й at 2 is a consonant and must not confuse anything.
    assert _words(parse_author_notation("МАЙно ДеАтти")) == [("майно", 1), ("деатти", 2)]


def test_yo_capital_counts_as_a_vowel():
    assert _words(parse_author_notation("ПартЁр")) == [("партёр", 4)]


def test_stressed_form_puts_combining_acute_after_the_vowel():
    assert stressed_form(AuthorStress("адалка", 2)) == "ада́лка"


def _write_docx(path, paragraphs):
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    document.save(path)


def test_read_docx_takes_the_section_after_the_heading_up_to_the_cast(tmp_path):
    path = tmp_path / "list.docx"
    _write_docx(path, [
        "Чтецы", "кто-то читает", "",
        "Ударения", "",
        "АдАлка", "БушУк/бушукИ", "Изельд ", "ЗвИрр'КрАА (как вороний крик)",
        "", "",
        "В ролях:", "Павел Филатов - рассказчик, ПазУзу",
    ])
    entries, stats = read_author_stress_docx(path)
    assert _words(entries) == [("адалка", 2), ("бушук", 3), ("бушуки", 5), ("звирр", 2), ("краа", 2)]
    assert stats["entries"] == 5
    assert stats["unknown"] == 1
    assert stats["unknown_words"] == ["Изельд"]
    assert stats["notes"] == 2  # both words of the line carry its note
    assert stats["end_reason"] == "blank_gap"  # two empty paragraphs before «В ролях» end the list


@pytest.mark.parametrize("heading", ["ТИТРЫ", "В ролях:", "Титры"])
def test_read_docx_stops_at_a_heading(tmp_path, heading):
    path = tmp_path / "list.docx"
    _write_docx(path, ["Ударения", "АдАлка", heading, "ПазУзу"])
    entries, stats = read_author_stress_docx(path)
    assert _words(entries) == [("адалка", 2)]
    assert stats["end_reason"] == "heading"


def test_read_docx_without_the_heading_raises(tmp_path):
    path = tmp_path / "list.docx"
    _write_docx(path, ["Просто текст", "АдАлка"])
    with pytest.raises(ValueError):
        read_author_stress_docx(path)


def test_read_docx_keeps_the_first_of_a_duplicate(tmp_path):
    path = tmp_path / "list.docx"
    _write_docx(path, ["Ударения", "АдАлка", "АдАлка"])
    entries, stats = read_author_stress_docx(path)
    assert len(entries) == 1
    assert stats["duplicates"] == 1


def test_upsert_creates_then_updates_author_pronunciation_rows():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import AuthorPronunciation
    from scripts.v2.import_author_stress import upsert_author_pronunciations

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with SessionLocal() as db:
        db.add(AuthorPronunciation(author_id="a1", term="Полумрак", stressed="па́ргорон", source="compendium"))
        db.commit()
        entries = [AuthorStress("полумрак", 6), AuthorStress("адалка", 2)]
        counts = upsert_author_pronunciations(db, author_id="a1", entries=entries)
        db.commit()
        assert counts == {"created": 1, "updated": 1, "unchanged": 0}
        rows = {r.term.lower(): r for r in db.query(AuthorPronunciation).all()}
        assert rows["полумрак"].stressed == "полумра́к"
        assert rows["полумрак"].source == "import_gdoc"
        assert rows["адалка"].stressed == "ада́лка"

        counts = upsert_author_pronunciations(db, author_id="a1", entries=entries)
        assert counts == {"created": 0, "updated": 0, "unchanged": 2}
        # Another author's rows are not touched.
        assert db.query(AuthorPronunciation).filter(AuthorPronunciation.author_id == "a2").count() == 0
