"""Turning a read chapter into the records the benchmark scores.

Three kinds of span come out of the markup and only one of them is evidence:

  * a coloured run whose colour the chapter's own legend names — scored;
  * a paragraph with no colour at all — scored as the Narrator, on the owner's
    guarantee that unmarked text is always and only his;
  * everything else — an uncoloured stretch beside a coloured one (the owner decided
    at the mixing desk whether «— сказал он» belongs to the speaker), or a colour the
    legend never introduced — dropped.

Dropped is not the same as forgotten: every count comes back with the records, so a
benchmark run always states how much of the chapter it declined to judge. A metric
that quietly shrinks its own denominator is worse than no metric.
"""
import pathlib

import pytest

from app.v2.gdoc_import import LegendEntry, Paragraph, Span
from app.v2.ground_truth import NARRATOR, ground_truth_records



class _Chapter:
    def __init__(self, legend, paragraphs):
        self.path = "тест.docx"
        self.title = "Байка тестовая"
        self.legend = legend
        self.paragraphs = paragraphs
        self.colours_without_legend = {}


def _para(ordinal, text, spans):
    return Paragraph(ordinal=ordinal, text=text, spans=spans)


LEGEND = {
    "H:cyan": LegendEntry(role="Сэр Ульганд", actor="Дмитрий Тихонов"),
    "H:red": LegendEntry(role="Азапба", actor=""),
}


def test_a_coloured_span_becomes_a_record_with_its_role_and_actor():
    text = "– А ну, мужичье, раздались!.."
    chapter = _Chapter(LEGEND, [_para(0, text, [Span(0, len(text), "H:cyan", True)])])

    records, stats = ground_truth_records(chapter)

    assert len(records) == 1
    assert records[0]["speaker"] == "Сэр Ульганд"
    assert records[0]["actor"] == "Дмитрий Тихонов"
    assert records[0]["text"] == text
    assert (records[0]["span_start"], records[0]["span_end"]) == (0, len(text))
    assert stats["scored"] == 1


def test_an_unmarked_paragraph_is_the_narrator():
    text = "Великан с рёвом упал на колени."
    chapter = _Chapter(LEGEND, [_para(0, text, [Span(0, len(text), None, True)])])

    records, stats = ground_truth_records(chapter)

    assert [r["speaker"] for r in records] == [NARRATOR]
    assert records[0]["actor"] == ""
    assert stats["scored"] == 1


def test_the_uncertain_tail_of_a_replica_is_dropped_and_counted():
    speech, tail = "– Не бойся, – прошептал Ульганд", ", пуская коня."
    chapter = _Chapter(LEGEND, [_para(0, speech + tail, [
        Span(0, len(speech), "H:cyan", True),
        Span(len(speech), len(speech + tail), None, False),
    ])])

    records, stats = ground_truth_records(chapter)

    assert [r["speaker"] for r in records] == ["Сэр Ульганд"]
    assert stats["dropped_uncertain"] == 1
    assert stats["scored"] == 1


def test_a_colour_the_legend_never_introduced_is_dropped_and_counted():
    """The owner's decision: build the legend per chapter, drop what it does not name.

    Colours are reused between tales — only the cycle's recurring leads keep one —
    so a colour missing here cannot be borrowed from a neighbouring chapter.
    """
    text = "– Кто здесь?"
    chapter = _Chapter(LEGEND, [_para(0, text, [Span(0, len(text), "S:abcdef", True)])])

    records, stats = ground_truth_records(chapter)

    assert records == []
    assert stats["dropped_no_legend"] == 1
    assert stats["colours_without_legend"] == {"S:abcdef": 1}


def test_a_legend_entry_without_a_role_cannot_score_anything():
    chapter = _Chapter(
        {"H:cyan": LegendEntry(role="", actor="")},
        [_para(0, "– Речь.", [Span(0, 7, "H:cyan", True)])],
    )

    records, stats = ground_truth_records(chapter)

    assert records == []
    assert stats["dropped_no_legend"] == 1


def test_stats_account_for_every_span():
    speech, tail = "– Да.", " – кивнул он."
    chapter = _Chapter(LEGEND, [
        _para(0, "Проза.", [Span(0, 6, None, True)]),
        _para(1, speech + tail, [
            Span(0, len(speech), "H:cyan", True),
            Span(len(speech), len(speech + tail), None, False),
        ]),
        _para(2, "– Чужой цвет.", [Span(0, 13, "S:123456", True)]),
    ])

    _records, stats = ground_truth_records(chapter)

    assert stats["scored"] + stats["dropped_uncertain"] + stats["dropped_no_legend"] == stats["spans_total"]
    assert stats["spans_total"] == 4
