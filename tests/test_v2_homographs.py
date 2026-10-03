"""«Спорные ударения»: the places where the reading depends on the sentence.

The base dictionary calls 522 words of «Крыльев» ambiguous — «уже», «замок», «руки» —
and the context layer chose a reading for each of their 5 342 occurrences. Confirming
5 342 decisions by hand is not review, it is retyping, so this groups them the way the
work actually splits:

- 400 words the model read the same way everywhere («время» 268 times out of 268):
  one confirmation for the word, not 268 for the places;
- 122 words it read differently in different places — «руки» 99 against 97 — where
  the sentence really does decide;
- and 95 places whose reading is the rare one for that word. That is where a mistake
  hides, and it is half an hour of work rather than a week.

The rare reading is the whole point of the ordering: a model that picks «уже́» 727
times and «у́же» 5 times is telling you which five to look at.
"""
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ScriptBook, ScriptChapter
from app.v2.homograph_ops import homograph_places, homograph_words
from app.v2.models import V2Segment, V2StressMark
from app.v2.stress_ops import StressTermError, _effective_marks_for, set_place_stress

BOOK = "book-1"
CH = "ch-1"

# «уже» stressed on offset 2 is «уже́», on offset 0 «у́же»; the fake dictionary calls
# it ambiguous and leaves «дом» alone.
LOOKUP = {"уже": [0, 2], "руки": [1, 3]}


def _lookup(word: str):
    return LOOKUP.get(word)


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine, autocommit=False, autoflush=False)() as session:
        session.add(ScriptBook(id=BOOK, title="Крылья", source_filename="k.txt", source_format="txt"))
        session.add(ScriptChapter(id=CH, book_id=BOOK, chapter_index=7, chapter_title="Глава 7"))
        yield session


def _place(db, ordinal: int, text: str, word: str, vowel: int, source: str = "context") -> str:
    segment = f"{CH}:{ordinal:05d}"
    db.add(V2Segment(id=segment, book_id=BOOK, chapter_id=CH, ordinal=ordinal, text=text, char_end=len(text)))
    start = text.lower().index(word)
    db.add(V2StressMark(id=str(uuid.uuid4()), segment_id=segment, word_start=start, word_end=start + len(word),
                        vowel_offset=vowel, source=source, version=1))
    return segment


class TestWords:
    def test_a_word_read_the_same_way_everywhere_is_one_decision(self, db):
        for i in range(3):
            _place(db, i, "Он уже ушёл.", "уже", 2)
        db.commit()

        rows = homograph_words(db, BOOK, dict_lookup=_lookup)

        assert len(rows) == 1
        assert rows[0]["word"] == "уже"
        assert rows[0]["places"] == 3
        assert rows[0]["mixed"] is False
        assert rows[0]["readings"] == [{"vowel_offset": 2, "count": 3, "form": "уже́"}]

    def test_a_word_read_two_ways_says_so_and_names_the_rare_one(self, db):
        for i in range(4):
            _place(db, i, "Он уже ушёл.", "уже", 2)
        _place(db, 9, "Пояс стал уже прежнего.", "уже", 0)
        db.commit()

        row = homograph_words(db, BOOK, dict_lookup=_lookup)[0]

        assert row["mixed"] is True
        assert row["places"] == 5
        assert [r["count"] for r in row["readings"]] == [4, 1]
        assert row["rare_places"] == 1

    def test_words_come_with_the_most_doubtful_first(self, db):
        """«Руки» 1:1 is a real fork; «уже» 4:1 is probably four right and one to check."""
        for i in range(4):
            _place(db, i, "Он уже ушёл.", "уже", 2)
        _place(db, 4, "Пояс стал уже.", "уже", 0)
        _place(db, 5, "Его руки дрожали.", "руки", 1)
        _place(db, 6, "Не марай руки.", "руки", 3)
        db.commit()

        rows = homograph_words(db, BOOK, dict_lookup=_lookup)

        # both are mixed; the one whose split is closest to even comes first
        assert [row["word"] for row in rows] == ["руки", "уже"]

    def test_a_word_the_dictionary_does_not_call_ambiguous_is_not_here(self, db):
        _place(db, 0, "Большой дом стоял.", "дом", 0)
        db.commit()

        assert homograph_words(db, BOOK, dict_lookup=_lookup) == []

    def test_only_the_context_layer_is_under_review(self, db):
        """A mark the author put there himself is a decision, not a proposal."""
        _place(db, 0, "Он уже ушёл.", "уже", 2, source="author")
        db.commit()

        assert homograph_words(db, BOOK, dict_lookup=_lookup) == []


class TestPlaces:
    def test_a_place_carries_its_sentence_and_both_readings(self, db):
        segment = _place(db, 3, "Пояс стал уже прежнего.", "уже", 0)
        db.commit()

        places = homograph_places(db, BOOK, "уже", dict_lookup=_lookup)

        assert len(places) == 1
        place = places[0]
        assert place["segment_id"] == segment
        assert place["chapter_index"] == 7
        assert place["text"] == "Пояс стал уже прежнего."
        assert place["word_start"] == 10 and place["word_end"] == 13
        assert place["chosen"] == 0
        assert place["options"] == [{"vowel_offset": 0, "form": "у́же"}, {"vowel_offset": 2, "form": "уже́"}]

    def test_the_rare_reading_comes_first(self, db):
        for i in range(3):
            _place(db, i, "Он уже ушёл.", "уже", 2)
        _place(db, 8, "Пояс стал уже прежнего.", "уже", 0)
        db.commit()

        places = homograph_places(db, BOOK, "уже", dict_lookup=_lookup)

        assert [place["chosen"] for place in places] == [0, 2, 2, 2]
        assert places[0]["rare"] is True and places[1]["rare"] is False

    def test_an_unknown_word_has_no_places(self, db):
        assert homograph_places(db, BOOK, "дом", dict_lookup=_lookup) == []


class TestDecidingOnePlace:
    """A homograph cannot be settled by a rule: «уже» is «уже́» here and «у́же» there.

    `apply_stress_rule` marks every occurrence in the book, which is right for a word
    the model read one way everywhere and destructive for one it did not. Reviewing
    the 122 mixed words needs a decision that reaches exactly one place.
    """

    def test_the_place_takes_the_new_reading_and_keeps_the_others(self, db):
        segment = _place(db, 0, "Пояс стал уже прежнего.", "уже", 2)
        # a second word in the same segment, already marked by the dictionary
        db.add(V2StressMark(id=str(uuid.uuid4()), segment_id=segment, word_start=0, word_end=4,
                            vowel_offset=1, source="dict", version=1))
        db.commit()

        set_place_stress(db, segment_id=segment, word_start=10, word_end=13, vowel_offset=0, actor_uid="u1")
        db.commit()

        marks = _effective_marks_for(db, [segment])[segment]
        assert (10, 13, 0, "operator") in [tuple(m) for m in marks]
        assert (0, 4, 1, "dict") in [tuple(m) for m in marks], "соседнее слово не должно потеряться"

    def test_the_operator_wins_over_the_model_next_time_the_queue_is_built(self, db):
        segment = _place(db, 0, "Пояс стал уже прежнего.", "уже", 2)
        db.commit()

        set_place_stress(db, segment_id=segment, word_start=10, word_end=13, vowel_offset=0, actor_uid="u1")
        db.commit()

        # the place is no longer a proposal, so it leaves the review
        assert homograph_places(db, BOOK, "уже", dict_lookup=_lookup) == []

    def test_an_offset_that_is_not_a_vowel_is_refused(self, db):
        segment = _place(db, 0, "Пояс стал уже прежнего.", "уже", 2)
        db.commit()

        with pytest.raises(StressTermError) as exc:
            set_place_stress(db, segment_id=segment, word_start=10, word_end=13, vowel_offset=1, actor_uid="u1")

        assert exc.value.code == "offset_not_on_vowel"

    def test_an_unknown_segment_is_refused(self, db):
        with pytest.raises(StressTermError) as exc:
            set_place_stress(db, segment_id="нет такого", word_start=0, word_end=3, vowel_offset=0, actor_uid="u1")

        assert exc.value.code == "segment_not_found"
