"""Денежные суммы, даты и часы сравниваются без догадок о неоднозначных обозначениях."""
import pytest

from app.services.asr_align import align_transcript, normalize_words, word_stream


@pytest.mark.parametrize(("written", "spoken"), [
    ("$21", "twenty one dollars"),
    ("$21.50", "twenty one dollars and fifty cents"),
    ("$1.05", "one dollar and five cents"),
    ("$2.5", "two dollars and fifty cents"),
    ("€3.01", "three euros and one cent"),
    ("£1.01", "one pound and one penny"),
    ("-$2", "minus two dollars"),
    ("2:05 p.m.", "two oh five pm"),
    ("12:30 AM", "twelve thirty am"),
    ("8:00 pm", "eight pm"),
    ("2026-03-08", "March eighth two thousand twenty six"),
    ("2026-03-08", "March eighth twenty twenty six"),
    ("1999-12-31", "December thirty first nineteen ninety nine"),
    ("March 8, 2026", "March eighth twenty twenty six"),
    ("year 1999", "year nineteen ninety-nine"),
    ("year 1905", "year nineteen oh five"),
    ("year 2000", "year twenty hundred"),
    ("year 2026", "year twenty twenty six"),
])
def test_explicit_formats_match_in_both_directions_with_word_timestamps(written, spoken):
    assert normalize_words(written, language="en") == normalize_words(spoken, language="en")
    for expected, heard in ((written, spoken), (spoken, written)):
        items = [{"word": word, "start": index * .5, "end": index * .5 + .4}
                 for index, word in enumerate(heard.split())]
        result = align_transcript([expected], [{"words": items}], language="en")
        line = result["lines"][0]
        assert line["matched"] and line["score"] == 1
        assert line["start"] == 0
        assert line["end"] == items[-1]["end"]


@pytest.mark.parametrize(("expected", "heard"), [
    ("$21.50", "twenty one dollars and fifteen cents"),
    ("$21.50", "twenty one euros and fifty cents"),
    ("$21", "twenty one"),
    ("$21.50", "twenty one point five zero"),
    ("£1.01", "one pound and ten pence"),
    ("2:05 pm", "two oh five am"),
    ("2:05 pm", "two fifty pm"),
    ("2026-03-08", "May eighth twenty twenty six"),
    ("2026-03-08", "March ninth twenty twenty six"),
    ("2026-03-08", "March eighth twenty twenty five"),
    ("year 1999", "year nineteen ninety eight"),
    ("year 21", "year twenty twenty one"),
    ("03/04/2026", "March fourth twenty twenty six"),
    ("2026-02-31", "February twenty eighth twenty twenty six"),
    ("14:05", "fourteen five"),
    ("12:60 pm", "twelve thirty pm"),
    ("$21.005", "twenty one dollars and five cents"),
    ("I paid USD 21 for this book", "I paid EUR 21 for this book"),
])
def test_changed_amount_currency_time_or_date_does_not_match(expected, heard):
    result = align_transcript([expected], [{"text": heard, "start": 0, "end": 6}], language="en")
    assert result["missing"] == [0]


def test_money_expansion_remains_inside_the_provider_word_interval():
    stream = word_stream([{"words": [{"word": "$21.50", "start": 3, "end": 6}]}], language="en")
    assert [word for word, _, _ in stream] == ["twenty", "one", "dollars", "and", "fifty", "cents"]
    assert stream[0][1] == 3 and stream[-1][2] == 6
    assert all(left[2] == right[1] for left, right in zip(stream, stream[1:]))


def test_clock_and_meridian_keep_their_separate_provider_intervals():
    stream = word_stream([{"words": [{"word": "2:05", "start": 1, "end": 4},
                                      {"word": "p.m.", "start": 5, "end": 6}]}], language="en")
    assert stream == [("two", 1., 2.), ("oh", 2., 3.), ("five", 3., 4.), ("pm", 5., 6.)]


@pytest.mark.parametrize("raw", ["03/04/2026", "2026-02-31", "14:05"])
def test_ambiguous_or_invalid_numeric_forms_remain_visible(raw):
    assert normalize_words(raw, language="en") == [raw]
