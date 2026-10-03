"""One shape of "numbered piece of text", fed from two places.

Attribution has to be measured on «Полумракские байки», because that is where the
owner's hand-marked reference is, and it has to run on «Крылья полумрака», because
that is the book being made. The байки are not a book in the system and never will
be — they are finished and voiced — so they arrive as docx paragraphs, while «Крылья»
arrives as rows of v2_segments.

If those two fed different code paths, the measurement would be of something other
than the thing that runs. So both become Units first, and the attribution knows
nothing else.

The two sources number differently on purpose and that is fine: the docx reader skips
the heading and the cast legend and counts from the first paragraph of prose, while
the segmenter counts the heading as segment zero. What must agree is the shape, not
the numbering.
"""
from app.v2.gdoc_import import LegendEntry, Paragraph, Span
from app.v2.segmenter import segment_chapter
from app.v2.units import Unit, units_from_chapter, units_from_segments


class _Chapter:
    def __init__(self, title, paragraphs):
        self.title = title
        self.path = f"{title}.docx"
        self.legend = {"H:cyan": LegendEntry(role="Сэр Ульганд", actor="Дмитрий Тихонов")}
        self.paragraphs = paragraphs


def test_segments_become_units():
    segments = segment_chapter("Глава 1. Раз\n\nПроза.\n", chapter_id="ch1")

    units = units_from_segments(segments)

    assert [u.id for u in units] == ["ch1:00000", "ch1:00001"]
    assert [u.text for u in units] == ["Глава 1. Раз", "Проза."]
    assert [u.ordinal for u in units] == [0, 1]
    assert all(u.chapter == "ch1" for u in units)


def test_docx_paragraphs_become_units():
    chapter = _Chapter("Байка 3", [
        Paragraph(ordinal=0, text="Проза.", spans=[Span(0, 6, None, True)]),
        Paragraph(ordinal=1, text="– Речь.", spans=[Span(0, 7, "H:cyan", True)]),
    ])

    units = units_from_chapter(chapter)

    assert [u.text for u in units] == ["Проза.", "– Речь."]
    assert [u.ordinal for u in units] == [0, 1]
    assert all(u.chapter == "Байка 3" for u in units)


def test_both_sources_produce_the_same_shape():
    """The point of the adapter: one type reaches the attribution, whatever fed it."""
    from_segments = units_from_segments(segment_chapter("Проза.\n", chapter_id="ch1"))
    from_docx = units_from_chapter(_Chapter("Байка 3", [
        Paragraph(ordinal=0, text="Проза.", spans=[Span(0, 6, None, True)]),
    ]))

    assert isinstance(from_segments[0], Unit) and isinstance(from_docx[0], Unit)
    assert from_segments[0].text == from_docx[0].text


def test_a_unit_knows_where_a_prediction_about_it_belongs():
    """A span the model returns has to find its way back to the chapter and paragraph
    the benchmark keys on, or it cannot be scored."""
    unit = units_from_chapter(_Chapter("Байка 3", [
        Paragraph(ordinal=7, text="– Речь.", spans=[]),
    ]))[0]

    record = unit.as_record(span_start=0, span_end=7, speaker="Сэр Ульганд")

    assert record["chapter"] == "Байка 3"
    assert record["ordinal"] == 7
    assert record["text"] == "– Речь."
    assert record["span_start"] == 0 and record["span_end"] == 7
    assert record["speaker"] == "Сэр Ульганд"


def test_an_empty_source_gives_no_units():
    assert units_from_segments([]) == []
    assert units_from_chapter(_Chapter("Байка 3", [])) == []
