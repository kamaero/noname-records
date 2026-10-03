"""Operator stress edits on v2: one word settled once lands everywhere, as new rows.

The seed is two chapters with a handful of marks in several states — a wrong
dictionary mark on «Дгарнин», a context guess on a homograph, a word nobody
answered — so each operation has something to change and something it must leave
alone.
"""
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Author, AuthorPronunciation, OperatorIntervention, ScriptBook, ScriptChapter
from app.pronunciation import parse_pronunciation_notes
from app.v2.models import V2Segment, V2StressMark
from app.v2.store import load_effective_stress, store_stress_marks
from app.v2.stress import StressMark, is_author_inflection, load_author_layer, offset_for_form
from app.v2.stress_ops import (
    StressTermError,
    apply_stress_rule,
    save_stress_term,
    skip_stress_word,
    stress_queue,
    unskip_stress_word,
    word_forms_in_book,
)

BOOK = "book-1"
AUTHOR = "author-1"
CH1, CH2 = "ch-1", "ch-2"
SEG_A = f"{CH1}:00000"   # «Дгарнин сидел в изгибе ветвей.»
SEG_B = f"{CH2}:00000"   # «Пупип увидел Дгарнина у замка.»
SEG_C = f"{CH2}:00001"   # «Дга́рнин молчал.» — a mark already in the text


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _mark(segment_id, start, end, vowel, source, version=1):
    return V2StressMark(id=str(uuid.uuid4()), segment_id=segment_id, word_start=start, word_end=end,
                        vowel_offset=vowel, source=source, version=version)


def _seed(db, *, author_id=AUTHOR, notes=""):
    db.add(Author(id=AUTHOR, name="Александр Белозёров", slug="belozerov"))
    db.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt",
                      author_id=author_id, pronunciation_notes=notes))
    db.add(ScriptChapter(id=CH1, book_id=BOOK, chapter_index=1, chapter_title="Глава 1"))
    db.add(ScriptChapter(id=CH2, book_id=BOOK, chapter_index=2, chapter_title="Глава 2"))
    texts = {
        SEG_A: (CH1, 0, "Дгарнин сидел в изгибе ветвей."),
        SEG_B: (CH2, 0, "Пупип увидел Дгарнина у замка."),
        SEG_C: (CH2, 1, "Дга́рнин молчал."),
    }
    for seg_id, (chapter_id, ordinal, text) in texts.items():
        db.add(V2Segment(id=seg_id, book_id=BOOK, chapter_id=chapter_id, ordinal=ordinal, text=text, char_end=len(text)))
    # SEG_A: v1 put the stress on the wrong vowel of «Дгарнин»; «сидел» and «изгибе» are fine; «ветвей» unanswered.
    db.add(_mark(SEG_A, 0, 7, 1, "dict"))
    db.add(_mark(SEG_A, 8, 13, 3, "dict"))
    db.add(_mark(SEG_A, 16, 22, 3, "context"))
    # SEG_B: «Пупип» by dict, «замка» guessed by context (a homograph in the dictionary), the rest unanswered.
    db.add(_mark(SEG_B, 0, 5, 3, "dict"))
    db.add(_mark(SEG_B, 24, 29, 1, "context"))
    db.commit()
    return db.get(ScriptBook, BOOK)


def _fake_lookup(word):
    return {"замка": [1, 4], "изгибе": 3}.get(word)


# --- the stem rule shared with the Resolver ------------------------------------------------

def test_inflection_rule_matches_what_the_resolver_matches():
    assert is_author_inflection("дгарнин", "дгарнин")
    assert is_author_inflection("дгарнин", "дгарнина")
    assert is_author_inflection("адалка", "адалки")        # final vowel is the ending
    assert not is_author_inflection("замок", "замка")      # a consonant stem does not decline this way
    assert not is_author_inflection("азей", "азеем")       # stem «азе» is too short to trust
    assert not is_author_inflection("адалка", "адалкиными")  # ending too long


def test_inflection_rule_leaves_a_dictionary_word_with_a_foreign_tail_alone():
    """«матери» → «материал» — не форма, а другое слово: «-иал» не окончание, и словарь
    слово знает. Производное выдуманного слова словарь не знает — оно за автором."""
    known = {"материал": 6, "банкир": 4, "банкнот": 5, "игуменья": 2}.get
    assert not is_author_inflection("матери", "материал", known=known)
    assert not is_author_inflection("банке", "банкир", known=known)
    assert not is_author_inflection("банке", "банкнот", known=known)
    assert is_author_inflection("матери", "матерью", known=known)
    assert is_author_inflection("игуменье", "игуменья", known=known)
    assert is_author_inflection("хилакток", "хилактокка", known=known)


def test_offset_carries_over_directly_or_by_vowel_ordinal():
    assert offset_for_form("дгарнин", 5, "дгарнина") == 5
    assert offset_for_form("дгарнин", 5, "дга́рнин") == 6   # the acute shifts the letters by one
    assert offset_for_form("замок", 3, "замка") == 4        # second vowel of the base → second vowel of the form
    assert offset_for_form("замок", 3, "змк") is None
    assert offset_for_form("замок", 2, "замка") is None     # not a vowel of the base


# --- finding and marking ----------------------------------------------------------------

def test_word_forms_in_book_finds_the_word_and_its_inflections_in_reading_order():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        found = word_forms_in_book(db, BOOK, "Дгарни́н")
        assert found == [(SEG_A, 0, 7, "Дгарнин"), (SEG_B, 13, 21, "Дгарнина"), (SEG_C, 0, 8, "Дга́рнин")]
        assert word_forms_in_book(db, BOOK, "") == []
        assert word_forms_in_book(db, BOOK, "никого") == []


def test_apply_stress_rule_writes_a_new_version_and_keeps_the_other_words():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        result = apply_stress_rule(db, book_id=BOOK, word="дгарнин", vowel_offset=5, source="operator")
        db.commit()

        assert result == {"segments_updated": 3, "occurrences": 3, "skipped": 0, "notes": []}
        # SEG_A: the wrong mark is replaced, the two others survive into the new version.
        effective = load_effective_stress(db, SEG_A)
        assert [(m.word_start, m.vowel_offset, m.source, m.version) for m in effective] == [
            (0, 5, "operator", 2), (8, 3, "dict", 2), (16, 3, "context", 2),
        ]
        # History is intact: the old row is still there under version 1.
        assert db.query(V2StressMark).filter_by(segment_id=SEG_A, version=1).count() == 3
        # SEG_B: the inflected «Дгарнина» gets the mark alongside the existing ones.
        assert [(m.word_start, m.vowel_offset, m.source) for m in load_effective_stress(db, SEG_B)] == [
            (0, 3, "dict"), (13, 5, "operator"), (24, 1, "context"),
        ]
        # SEG_C had no rows; the text-marked surface gets the vowel by ordinal (the acute shifts it).
        assert [(m.word_start, m.vowel_offset, m.version) for m in load_effective_stress(db, SEG_C)] == [(0, 6, 1)]


def test_apply_stress_rule_rejects_an_offset_that_is_not_a_vowel():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        try:
            apply_stress_rule(db, book_id=BOOK, word="дгарнин", vowel_offset=4)
        except StressTermError as exc:
            assert exc.code == "offset_not_on_vowel"
        else:
            raise AssertionError("expected StressTermError")


def test_apply_stress_rule_skips_a_form_it_cannot_place_and_says_so():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        # «адорази́» — stressed on the final vowel. «Адорази» carries it over directly;
        # «Адоразь» is an inflection by the stem rule but has no vowel to take it.
        text = "Адорази и Адоразь молчали."
        seg = f"{CH1}:00001"
        db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=CH1, ordinal=1, text=text, char_end=len(text)))
        db.commit()
        result = apply_stress_rule(db, book_id=BOOK, word="адорази", vowel_offset=6)
        db.commit()

        assert (result["occurrences"], result["skipped"], result["segments_updated"]) == (2, 1, 1)
        assert result["notes"] == [f"{seg}: «Адоразь» — не удалось перенести ударение на эту форму"]
        assert [(m.word_start, m.vowel_offset, m.source) for m in load_effective_stress(db, seg)] == [(0, 6, "operator")]


# --- the term: notes / profile + marks + audit ------------------------------------------

def test_save_stress_term_for_the_book_writes_the_v1_notes_line_and_the_marks():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, notes="пупип=пупи́п\n")
        result = save_stress_term(db, book=book, word="Дгарнин", stressed="Дгарни́н", scope="book", actor_uid="u1", actor_name="Автор")
        db.commit()

        assert {k: result[k] for k in ("word", "stressed", "scope", "segments_updated", "occurrences", "skipped")} == {
            "word": "дгарнин", "stressed": "Дгарни́н", "scope": "book", "segments_updated": 3, "occurrences": 3, "skipped": 0,
        }
        assert parse_pronunciation_notes(book.pronunciation_notes) == {"пупип": "пупи́п", "дгарнин": "Дгарни́н"}
        assert db.query(AuthorPronunciation).count() == 0, "scope=book must not touch the profile"

        row = db.query(OperatorIntervention).one()
        assert row.action_type == "v2_stress_term" and row.actor_user_id == "u1" and row.actor_name == "Автор"
        assert "«дгарнин»" in row.reason
        assert '"previous_notes": "пупип=пупи́п\\n"' in row.payload_json
        assert '"scope": "book"' in row.payload_json


def test_save_stress_term_for_the_author_writes_the_profile_and_corrects_it_on_a_second_save():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db)
        save_stress_term(db, book=book, word="Дгарнин", stressed="Дга́рнин", scope="author", actor_uid="u1")
        db.commit()
        assert book.pronunciation_notes == "", "scope=author leaves a book without a line for the word alone"
        row = db.query(AuthorPronunciation).one()
        assert (row.term, row.stressed, row.source) == ("Дгарнин", "Дга́рнин", "book")
        assert [m.vowel_offset for m in load_effective_stress(db, SEG_A) if m.word_start == 0] == [2]

        save_stress_term(db, book=book, word="Дгарнин", stressed="Дгарни́н", scope="author", actor_uid="u1")
        db.commit()
        assert db.query(AuthorPronunciation).one().stressed == "Дгарни́н"
        assert [m.vowel_offset for m in load_effective_stress(db, SEG_A) if m.word_start == 0] == [5]
        assert db.query(OperatorIntervention).count() == 2


def test_save_stress_term_for_the_author_refreshes_a_book_line_that_already_exists():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, notes="дгарнин=дга́рнин\n")
        save_stress_term(db, book=book, word="дгарнин", stressed="дгарни́н", scope="author", actor_uid="u1")
        assert parse_pronunciation_notes(book.pronunciation_notes) == {"дгарнин": "дгарни́н"}


def test_save_stress_term_validation_codes():
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, author_id="")
        cases = [
            (dict(word="", stressed="дгарни́н", scope="book"), "word_and_stressed_required"),
            (dict(word="дгарнин", stressed="пупип", scope="book"), "stressed_does_not_match_word"),
            (dict(word="дгарнин", stressed="дгарнин", scope="book"), "no_vowel_marked"),
            (dict(word="дгарнин", stressed="дгарни́н", scope="galaxy"), "bad_scope"),
            (dict(word="дгарнин", stressed="дгарни́н", scope="author"), "book_has_no_author"),
        ]
        for kwargs, code in cases:
            try:
                save_stress_term(db, book=book, actor_uid="u1", **kwargs)
            except StressTermError as exc:
                assert exc.code == code, kwargs
            else:
                raise AssertionError(f"expected {code} for {kwargs}")
        assert db.query(OperatorIntervention).count() == 0


# --- the queue ---------------------------------------------------------------------------

def test_stress_queue_lists_unmarked_words_and_context_resolved_homographs():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        queue = stress_queue(db, BOOK, dict_lookup=_fake_lookup)

    assert queue == {
        "scope": "all",
        "has_more": {"unresolved": False, "homographs": False},
        "unresolved": [
            {"word": "ветвей", "count": 1, "sample_segment_id": SEG_A, "sample_chapter_index": 1},
            {"word": "дгарнина", "count": 1, "sample_segment_id": SEG_B, "sample_chapter_index": 2},
            {"word": "увидел", "count": 1, "sample_segment_id": SEG_B, "sample_chapter_index": 2},
        ],
        "homographs": [
            {"word": "замка", "count": 1, "sample_segment_id": SEG_B, "sample_chapter_index": 2},
        ],
        # «Дга́рнин» carries its mark in the text and counts as marked without a row.
        # SEG_C carries only an acute in its text and no mark row: no layer has run on
        # it, so «молчал» is not a residue yet — the segment is counted as unprocessed.
        # «дгарнина» never appears in lower case and the dictionary does not know it:
        # one invention among three unstressed words.
        "counts": {"unprocessed_segments": 1, "marked": 5, "unresolved": 3, "homographs": 1,
                   "unresolved_words": 3, "homograph_words": 1, "author_words": 1,
                   "common_words": 2, "name_words": 1, "skipped_words": 0},
    }


def test_stress_queue_groups_by_word_most_frequent_first_and_honours_the_limit():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        for i in range(3):
            seg = f"{CH1}:{i + 10:05d}"
            db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=CH1, ordinal=i + 10, text="Ветвей и ветвей.", char_end=16))
            # One mark per segment so the layers count as having run on it.
            store_stress_marks(db, segment_id=seg, marks=[StressMark(0, 6, 4, "dict")])
        db.commit()
        queue = stress_queue(db, BOOK, limit=1, dict_lookup=_fake_lookup)
        assert queue["unresolved"] == [{"word": "ветвей", "count": 4, "sample_segment_id": SEG_A, "sample_chapter_index": 1}]
        assert queue["counts"]["unresolved"] == 6 and queue["counts"]["unresolved_words"] == 3


def test_stress_queue_drops_a_word_once_a_rule_marks_it():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        apply_stress_rule(db, book_id=BOOK, word="дгарнин", vowel_offset=5)
        db.commit()
        words = [row["word"] for row in stress_queue(db, BOOK, dict_lookup=_fake_lookup)["unresolved"]]
        assert "дгарнина" not in words and "ветвей" in words


# --- the author layer from the database --------------------------------------------------

def test_author_layer_merges_notes_over_profile_over_json(tmp_path):
    (tmp_path / "list.json").write_text(
        '{"author_id": "", "entries": [{"word": "дгарнин", "vowel_index": 2}, {"word": "пупип", "vowel_index": 1}, {"word": "азей", "vowel_index": 2}]}',
        encoding="utf-8",
    )
    (tmp_path / "other.json").write_text(
        '{"author_id": "someone-else", "entries": [{"word": "чужой", "vowel_index": 1}]}', encoding="utf-8",
    )
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, notes="дгарнин=дгарни́н\n")
        db.add(AuthorPronunciation(author_id=AUTHOR, term="Дгарнин", stressed="Дга́рнин", variants="[]"))
        db.add(AuthorPronunciation(author_id=AUTHOR, term="Пупип", stressed="Пупи́п", variants="[]"))
        db.commit()
        layer = load_author_layer(db, book, json_dir=tmp_path)

    assert layer == {"дгарнин": 5, "пупип": 3, "азей": 2}
    # notes (5) beat the profile (2 from «Дга́рнин»), the profile (3) beats the json (1), the json fills the rest;
    # a list stamped for another author is ignored.


def test_author_layer_without_an_author_is_notes_plus_json(tmp_path):
    SessionLocal = _session()
    with SessionLocal() as db:
        book = _seed(db, author_id="", notes="дгарнин=дгарни́н\n")
        assert load_author_layer(db, book, json_dir=tmp_path) == {"дгарнин": 5}
        assert load_author_layer(db, book, json_dir=tmp_path / "missing") == {"дгарнин": 5}


# --- «Имена и выдумки»: the residue the author actually wants ------------------------------

def test_the_names_scope_keeps_the_words_that_are_only_ever_capitalised():
    """The author asked for «words the dictionaries do not know» and that is not the filter.

    On «Крыльях» 29 784 of 30 304 unstressed words are unknown to the dictionaries —
    the base dictionary holds words whose stress is *not* obvious, so «даже» and
    «сказал» are absent from it too. What separates «Бимькмолепус» from «даже» is the
    capital letter in the middle of a sentence: 2 136 words instead of 29 784.
    """
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        queue = stress_queue(db, BOOK, scope="names", dict_lookup=_fake_lookup)

    # «ветвей» and «увидел» are ordinary words; «Дгарнина» never appears in lower case
    assert [row["word"] for row in queue["unresolved"]] == ["дгарнина"]


def test_author_and_common_scopes_partition_every_unresolved_word_and_keep_legacy_views():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        all_queue = stress_queue(db, BOOK, scope="all", dict_lookup=_fake_lookup)
        names_queue = stress_queue(db, BOOK, scope="names", dict_lookup=_fake_lookup)
        common_queue = stress_queue(db, BOOK, scope="common", dict_lookup=_fake_lookup)

    all_words = {row["word"] for row in all_queue["unresolved"]}
    author_words = {row["word"] for row in names_queue["unresolved"]}
    common_words = {row["word"] for row in common_queue["unresolved"]}

    assert all_queue["scope"] == "all" and names_queue["scope"] == "names"
    assert author_words == {"дгарнина"}
    assert common_words == {"ветвей", "увидел"}
    assert author_words.isdisjoint(common_words)
    assert author_words | common_words == all_words
    assert all_queue["counts"]["author_words"] == all_queue["counts"]["name_words"] == len(author_words)
    assert all_queue["counts"]["common_words"] == len(common_words)
    assert all_queue["counts"]["author_words"] + all_queue["counts"]["common_words"] == all_queue["counts"]["unresolved_words"]
    # Scope changes only the unresolved list; context-resolved homographs remain reviewable.
    assert names_queue["homographs"] == common_queue["homographs"] == all_queue["homographs"]


def test_a_name_the_dictionary_knows_is_not_an_invention():
    """«Москва» is capitalised too, and nobody needs to be asked about it."""
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        seg = f"{CH1}:00020"
        db.add(V2Segment(id=seg, book_id=BOOK, chapter_id=CH1, ordinal=20, text="Потом была Москва.", char_end=18))
        store_stress_marks(db, segment_id=seg, marks=[StressMark(0, 5, 1, "dict")])
        db.commit()

        names = stress_queue(db, BOOK, scope="names", dict_lookup=lambda w: 1 if w == "москва" else _fake_lookup(w))

    assert "москва" not in [row["word"] for row in names["unresolved"]]


def test_the_list_can_be_paged_past_the_first_screen():
    """The cap of 200 by frequency is why the author never saw his own words: they are rare."""
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)

        first = stress_queue(db, BOOK, limit=2, dict_lookup=_fake_lookup)
        second = stress_queue(db, BOOK, limit=2, offset=2, dict_lookup=_fake_lookup)

    assert [row["word"] for row in first["unresolved"]] == ["ветвей", "дгарнина"]
    assert first["has_more"]["unresolved"] is True
    assert [row["word"] for row in second["unresolved"]] == ["увидел"]
    assert second["has_more"]["unresolved"] is False


def test_a_word_the_author_waves_off_leaves_the_queue_for_good():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        skip_stress_word(db, book_id=BOOK, word="Ветвей", actor_uid="u1")
        db.commit()

        queue = stress_queue(db, BOOK, dict_lookup=_fake_lookup)

    assert "ветвей" not in [row["word"] for row in queue["unresolved"]]
    assert queue["counts"]["skipped_words"] == 1


def test_a_waved_off_word_can_come_back():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        skip_stress_word(db, book_id=BOOK, word="ветвей", actor_uid="u1")
        db.commit()
        unskip_stress_word(db, book_id=BOOK, word="ветвей")
        db.commit()

        queue = stress_queue(db, BOOK, dict_lookup=_fake_lookup)

    assert "ветвей" in [row["word"] for row in queue["unresolved"]]


def test_waving_off_the_same_word_twice_is_not_two_rows():
    SessionLocal = _session()
    with SessionLocal() as db:
        _seed(db)
        skip_stress_word(db, book_id=BOOK, word="ветвей", actor_uid="u1")
        skip_stress_word(db, book_id=BOOK, word="Ветвей", actor_uid="u1")
        db.commit()

        assert stress_queue(db, BOOK, dict_lookup=_fake_lookup)["counts"]["skipped_words"] == 1
