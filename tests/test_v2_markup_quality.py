"""A file the owner never painted is a manuscript, not ground truth."""
from app.v2.gdoc_import import Chapter, LegendEntry, Paragraph, Span
from app.v2.ground_truth import is_marked, markup_quality


def _chapter(legend, coloured_dialogue, plain_dialogue):
    paragraphs = []
    for i in range(coloured_dialogue):
        paragraphs.append(Paragraph(i, "– Да.", [Span(0, 5, "H:red", True)]))
    for j in range(plain_dialogue):
        paragraphs.append(Paragraph(coloured_dialogue + j, "– Нет.", [Span(0, 6, None, True)]))
    paragraphs.append(Paragraph(len(paragraphs), "Он ушёл.", [Span(0, 8, None, True)]))
    return Chapter(path="x", title="t", legend=legend, paragraphs=paragraphs)


def test_unpainted_file_is_not_marked():
    chapter = _chapter({}, 0, 40)
    assert markup_quality(chapter) == {"legend": 0, "dialogue": 40, "coloured": 0, "share": 0.0}
    assert not is_marked(chapter)


def test_half_painted_file_is_marked_and_narration_does_not_count_as_dialogue():
    chapter = _chapter({"H:red": LegendEntry(role="Пупип")}, 30, 20)
    quality = markup_quality(chapter)
    assert quality["dialogue"] == 50 and quality["coloured"] == 30
    assert is_marked(chapter)
    assert not is_marked(chapter, min_share=0.7)
