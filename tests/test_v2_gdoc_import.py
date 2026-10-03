"""Reading the owner's hand-marked «Полумракские байки» into ground truth.

The markup is a Google Docs export: each role's replicas carry that role's colour,
and a legend at the top of the chapter maps colour to role and role to actor.

Two things the owner guarantees, and they are the only things scored:
  * a coloured run is that character speaking;
  * a paragraph with no colour anywhere in it is the Narrator — always and only.

One thing that is deliberately NOT a signal: whether an attribution clause inside a
replica («— сказал он») is coloured. That was decided at mixing time, not at markup
time — such clauses are dropped from the mix when the actor already plays them, and
kept when they carry meaning. So an uncoloured stretch inside a partly coloured
paragraph is ambiguous between the Narrator and the speaker's own aside, and is
marked uncertain so the benchmark can leave it out instead of punishing a model for
the owner's mixing decisions.
"""
import pathlib

import pytest

from app.v2.gdoc_import import legend_bounds, parse_legend_line, read_chapter, spans_from_runs



class TestLegendLine:
    """Four shapes taken from the real files, not invented."""

    def test_role_and_actor(self):
        entry = parse_legend_line("Сэр Ульганд - Дмитрий Тихонов", coloured_text="Сэр Ульганд - Дмитрий Тихонов")

        assert entry.role == "Сэр Ульганд"
        assert entry.actor == "Дмитрий Тихонов"

    def test_a_missing_actor_leaves_the_role_alone(self):
        """«Банкир Субиух-» — the owner warned some legends lack the actor, and the
        dash is stuck to the name with no space."""
        entry = parse_legend_line("Банкир Субиух-", coloured_text="Банкир Субиух-")

        assert entry.role == "Банкир Субиух"
        assert entry.actor == ""

    def test_the_actor_is_the_last_part_when_the_line_has_three(self):
        """«Рассказчик - Инвнехлизад - Евгений Остапович»: the narrator of the cycle
        is Инвнехлизад, voiced by Остапович. The middle part is who, not by whom."""
        entry = parse_legend_line(
            "Рассказчик - Инвнехлизад - Евгений Остапович",
            coloured_text="Рассказчик - Инвнехлизад - Евгений Остапович",
        )

        assert entry.role == "Рассказчик"
        assert entry.actor == "Евгений Остапович"
        assert entry.note == "Инвнехлизад"

    def test_when_only_part_of_the_line_is_coloured_that_part_is_the_role(self):
        """«Рассказчик (Дегатти в интерлюдии) - Роман Сомов» carries the colour on
        the parenthetical alone, so the colour belongs to Дегатти, not to Рассказчик."""
        entry = parse_legend_line(
            "Рассказчик (Дегатти в интерлюдии) - Роман Сомов",
            coloured_text="Дегатти в интерлюдии",
        )

        assert entry.role == "Дегатти в интерлюдии"
        assert entry.actor == "Роман Сомов"


class TestSpans:
    def test_a_paragraph_with_no_colour_is_the_narrator(self):
        runs = [(None, "Великан с рёвом упал на колени.")]
        spans = spans_from_runs(runs)

        assert len(spans) == 1
        assert spans[0].speaker_colour is None
        assert spans[0].certain is True

    def test_a_fully_coloured_paragraph_is_one_span(self):
        line = "– А ну, мужичье, раздались!.."
        spans = spans_from_runs([("H:cyan", line)])

        assert [(s.start, s.end, s.speaker_colour, s.certain) for s in spans] == [
            (0, len(line), "H:cyan", True)
        ]

    def test_the_gap_beside_a_coloured_run_is_not_scored(self):
        """«– Не бойся, – прошептал Ульганд» + «, пуская своего Ветронога…».

        The tail may be the Narrator or Ульганд's own aside; the owner decided that
        at mixing time. Certain enough to keep, not certain enough to score.
        """
        runs = [("H:cyan", "– Не бойся, – прошептал Ульганд"), (None, ", пуская коня.")]
        spans = spans_from_runs(runs)

        assert [(s.speaker_colour, s.certain) for s in spans] == [
            ("H:cyan", True),
            (None, False),
        ]

    def test_adjacent_runs_of_one_colour_become_one_span(self):
        runs = [("H:cyan", "– Что случилось, "), ("H:cyan", "добрые люди?")]
        spans = spans_from_runs(runs)

        assert len(spans) == 1
        assert (spans[0].start, spans[0].end) == (0, 29)

    def test_offsets_address_the_paragraph_text(self):
        runs = [(None, "Он сказал: "), ("H:red", "– Нет."), (None, " И вышел.")]
        text = "".join(t for _c, t in runs)
        spans = spans_from_runs(runs)

        assert [text[s.start:s.end] for s in spans] == ["Он сказал: ", "– Нет.", " И вышел."]
        assert [s.certain for s in spans] == [False, True, False]

    def test_an_empty_paragraph_yields_nothing(self):
        assert spans_from_runs([]) == []
        assert spans_from_runs([(None, "")]) == []


class TestLegendBoundary:
    """Where the cast list stops and the tale starts.

    A legend line is short and ENDS with a dash («Згагб -», «Банкир Субиух-»); a line
    of dialogue also carries a dash but STARTS with it, and prose carries none. Getting
    this backwards swallows the whole chapter into the legend, which is exactly what a
    first pass over these files did.
    """

    def test_the_legend_stops_at_the_first_prose(self):
        paragraphs = [
            "Байка двадцать вторая",
            "",
            "Згагб -",
            "Дазиптовд - Ольга Зубкова",
            "",
            "3799 год до Н.Э., Полумрак, Башня Душ.",
            "Банкиры водили хоровод и пели. " + "Они обожали праздник. " * 12,
        ]
        assert legend_bounds(paragraphs) == (2, 4)

    def test_a_line_of_dialogue_does_not_look_like_a_legend_line(self):
        paragraphs = [
            "Байка третья",
            "",
            "Сэр Ульганд - Дмитрий Тихонов",
            "– А ну, мужичье, раздались!.. – пустил вперед коня рыцарь.",
        ]
        assert legend_bounds(paragraphs) == (2, 3)

    def test_a_chapter_with_no_legend_at_all(self):
        assert legend_bounds(["Заголовок", "", "Длинная проза без всякой легенды."]) == (0, 0)
