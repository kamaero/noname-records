"""Английский профиль не должен скрывать пропуски за русскими правилами и похожими буквами."""
import pytest

from app.services.asr_align import align_transcript, normalize_words, word_stream


def _spoken(*texts):
    return [{"text": text, "start": index * 5.0, "end": index * 5.0 + 4.0}
            for index, text in enumerate(texts)]


@pytest.mark.parametrize(("written", "heard"), [
    ("21", "twenty one"),
    ("21st", "twenty first"),
    ("12th", "twelfth"),
    ("100th", "one hundredth"),
    ("1,234", "one thousand two hundred thirty four"),
    ("2,000,000", "two million"),
    ("0", "zero"),
    ("3.14", "three point one four"),
    ("-12", "minus twelve"),
    ("+12", "plus twelve"),
    ("Dr. O’Neil", "Doctor O'Neil"),
    ("Mr. West", "Mister West"),
    ("Mrs. West", "Missus West"),
    ("We’re ready", "we are ready"),
    ("I’m ready", "I am ready"),
    ("We’ve arrived", "we have arrived"),
    ("I’ll return", "I will return"),
    ("I can’t return", "I cannot return"),
    ("We won’t return", "we will not return"),
    ("It isn’t ready", "it is not ready"),
    ("“Don’t leave,” said O’Neil.", "Do not leave said O'Neil"),
    ("Rene\u0301e and José", "Renée and José"),
])
def test_english_equivalent_forms(written, heard):
    assert normalize_words(written, language="en") == normalize_words(heard, language="en")
    for expected, transcript in ((written, heard), (heard, written)):
        result = align_transcript([expected], _spoken(transcript), language="en")
        assert result["missing"] == []
        assert result["lines"][0]["score"] == 1.0


@pytest.mark.parametrize(("written", "heard"), [
    ("105", "one hundred and five"),
    ("1,005", "one thousand and five"),
])
def test_british_number_conjunction_is_optional(written, heard):
    for expected, transcript in ((written, heard), (heard, written)):
        assert align_transcript([expected], _spoken(transcript), language="en")["missing"] == []


@pytest.mark.parametrize(("left", "right"), [
    ("we’re", "were"),
    ("he’ll", "hell"),
    ("he’s", "he is"),
    ("he’d", "he would"),
    ("O’Neil", "Oneil"),
    ("Renée", "Renee"),
    ("007", "7"),
    ("1,23", "123"),
    ("11st", "11th"),
    ("R2D2", "R two D two"),
    ("12abc", "12 abc"),
    ("3.14", "314"),
    ("-12", "12"),
    ("1234567890", "1234567891"),
])
def test_ambiguous_names_and_numbers_remain_distinct(left, right):
    assert normalize_words(left, language="en") != normalize_words(right, language="en")


@pytest.mark.parametrize(("expected", "heard"), [
    ("No", "Now"),
    ("We’re ready", "Were ready"),
    ("I can’t open the old wooden door", "I can open the old wooden door"),
    ("I can open the old wooden door", "I cannot open the old wooden door"),
    ("I will enter the quiet room", "I will not enter the quiet room"),
    ("There are twenty two keys", "There are twenty three keys"),
    ("We counted 21 silver coins", "We counted 12 silver coins"),
    ("The first visitor has arrived", "The third visitor has arrived"),
    ("Turn to page one two", "Turn to page two one"),
    ("007", "7"),
])
def test_fuzzy_search_does_not_hide_a_short_word_number_or_negation_error(expected, heard):
    result = align_transcript([expected], _spoken(heard), language="en")
    assert result["missing"] == [0]
    assert result["lines"][0]["takes"] == []


def test_digit_expansion_retains_word_times_and_line_boundaries():
    spoken = [{"words": [{"word": "21", "start": 1.0, "end": 3.0},
                         {"word": "keys", "start": 3.5, "end": 4.0}]}]
    assert word_stream(spoken, language="en") == [
        ("twenty", 1.0, 2.0), ("one", 2.0, 3.0), ("keys", 3.5, 4.0),
    ]
    line = align_transcript(["twenty one keys"], spoken, language="en")["lines"][0]
    assert (line["start"], line["end"], line["score"]) == (1.0, 4.0, 1.0)


def test_word_numbers_match_written_digits_without_changing_provider_times():
    spoken = [{"words": [{"word": "twenty", "start": 1.0, "end": 1.4},
                         {"word": "one", "start": 2.1, "end": 2.5}]}]
    line = align_transcript(["21"], spoken, language="en")["lines"][0]
    assert (line["start"], line["end"], line["score"]) == (1.0, 2.5, 1.0)


def test_contraction_expansion_stays_inside_its_provider_word_interval():
    spoken = [{"words": [{"word": "can’t", "start": 2.0, "end": 3.0}]}]
    assert word_stream(spoken, language="en") == [("can", 2.0, 2.5), ("not", 2.5, 3.0)]


def test_repeated_number_line_does_not_steal_the_next_identical_line():
    result = align_transcript(["I have 21 keys", "I have twenty one keys"],
                              _spoken("I have twenty-one keys", "I have 21 keys"), language="en")
    assert result["missing"] == []
    assert [len(line["takes"]) for line in result["lines"]] == [1, 1]
    assert result["lines"][1]["start"] == 5.0


def test_repeat_and_later_number_line_keep_separate_takes():
    result = align_transcript(["I have 21 keys", "I have 22 keys"],
                              _spoken("I have 21 keys", "I have twenty one keys", "I have 22 keys"),
                              language="en")
    assert result["missing"] == []
    assert [len(line["takes"]) for line in result["lines"]] == [2, 1]
    assert result["lines"][1]["start"] == 10.0


def test_missing_number_line_keeps_later_read_line_in_place():
    result = align_transcript(["I have 21 keys", "I have 22 keys", "The boat has left the harbor"],
                              _spoken("I have twenty two keys", "The boat has left the harbor"),
                              language="en")
    assert result["missing"] == [0]
    assert result["lines"][1]["start"] == 0.0
    assert result["lines"][2]["start"] == 5.0


@pytest.mark.parametrize("answer", ["No", "Not a chance"])
def test_negative_answer_does_not_invalidate_the_preceding_line(answer):
    result = align_transcript(["The boat has left the harbor", answer],
                              _spoken("The boat has left the harbor", answer), language="en")
    assert result["missing"] == []


def test_russian_remains_the_default_and_english_is_explicit():
    text = "Ёж увидел 21-й вагон"
    assert normalize_words(text) == normalize_words(text, language="ru")
    assert normalize_words("21") == ["двадцать", "один"]
    assert normalize_words("21", language="en") == ["twenty", "one"]
    assert align_transcript([text], _spoken(text)) == align_transcript([text], _spoken(text), language="ru")


def test_unreasonably_long_identifiers_do_not_raise_or_expand():
    identifier = "9" * 5000
    assert normalize_words(identifier, language="en") == [identifier]


def test_unsupported_alignment_language_fails_even_on_empty_input():
    with pytest.raises(ValueError, match="Unsupported ASR alignment language"):
        align_transcript([], [], language="de")
