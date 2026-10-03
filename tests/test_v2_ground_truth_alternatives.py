"""One colour, several roles: the ground truth accepts any of them, the scorer too."""
from app.v2.bench import score
from app.v2.gdoc_import import Chapter, LegendEntry, Paragraph, Span
from app.v2.ground_truth import acceptable_roles, ground_truth_records


class _Resolver:
    def __init__(self):
        self.unresolved = {}

    def resolve(self, name):
        return name


def _chapter():
    shared = "S:b45f06"
    legend = {shared: LegendEntry(role="Садим", actor="А"), "H:green": LegendEntry(role="Рассказчик", actor="Л", note="Инвнехлизад")}
    legend_all = {
        shared: [LegendEntry(role="Коруак", actor="А"), LegendEntry(role="Садим", actor="А")],
        "H:green": [LegendEntry(role="Рассказчик", actor="Л", note="Инвнехлизад")],
    }
    paragraphs = [
        Paragraph(0, "– Нет! – фыркнул Коруак.", [Span(0, 24, shared, True)]),
        Paragraph(1, "– Да, – сказал Инвнехлизад.", [Span(0, 27, "H:green", True)]),
    ]
    return Chapter(path="x", title="t", legend=legend, legend_all=legend_all, paragraphs=paragraphs)


def test_every_role_behind_a_colour_is_acceptable():
    chapter = _chapter()
    assert acceptable_roles(chapter, "S:b45f06") == ["Садим", "Коруак"]
    assert acceptable_roles(chapter, "H:green") == ["Рассказчик", "Инвнехлизад"]


def test_records_carry_alternatives_and_scorer_accepts_any():
    records, _ = ground_truth_records(_chapter())
    assert records[0]["alternatives"] == ["Садим", "Коруак"]
    predicted = [
        {**records[0], "speaker": "Коруак"},
        {**records[1], "speaker": "Инвнехлизад"},
    ]
    report = score(records, predicted, resolver=_Resolver())
    assert report.accuracy == 1.0
    wrong = [{**records[0], "speaker": "Мимзенов"}, {**records[1], "speaker": "Рассказчик"}]
    report = score(records, wrong, resolver=_Resolver())
    assert 0 < report.accuracy < 1
    assert report.disagreements[0]["expected"] == "Садим|Коруак"
