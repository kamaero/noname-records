"""Scoring an attribution against the owner's markup.

Measured per character of text, not per paragraph and not per span. The reason is the
markup itself: the owner painted a replica's boundary by hand and decided at the mixing
desk whether «— сказал он» belonged inside it, so a model that finds the right speaker
but ends the span two words early is right about the thing that matters. Per-character
scoring degrades gracefully where per-span scoring would fail the whole replica.

Only characters covered by a certain ground-truth span are scored. Regions the markup
declines to assert — the ambiguous tail beside a replica, a colour with no legend — are
absent from the records and therefore absent from both numerator and denominator. A
prediction there is neither rewarded nor punished.
"""
from app.v2.aliases import DEGATTI, AliasResolver
from app.v2.bench import score

CAST = {"куйбу дегатти": "Куйбу Дегатти", "вукьрадух": "Вукьрадух", "инвнехлизад": "Инвнехлизад"}


def _resolver():
    return AliasResolver(cast=CAST, merges=[DEGATTI])


def _rec(text, start, end, speaker, ordinal=0, chapter="Байка 1"):
    return {
        "chapter": chapter, "ordinal": ordinal, "text": text,
        "span_start": start, "span_end": end, "speaker": speaker,
    }


LINE = "– Здравствуй, – сказал Вукьрадух."


class TestAccuracy:
    def test_an_exact_match_scores_everything(self):
        gold = [_rec(LINE, 0, len(LINE), "Вукьрадух")]

        report = score(gold, list(gold), resolver=_resolver())

        assert report.accuracy == 1.0
        assert report.covered_chars == len(LINE)

    def test_a_wrong_speaker_scores_nothing_over_that_span(self):
        gold = [_rec(LINE, 0, len(LINE), "Вукьрадух")]
        guess = [_rec(LINE, 0, len(LINE), "Инвнехлизад")]

        report = score(gold, guess, resolver=_resolver())

        assert report.accuracy == 0.0
        assert report.correct_chars == 0

    def test_a_boundary_off_by_a_few_words_keeps_most_of_the_credit(self):
        """The whole reason for counting characters rather than spans."""
        gold = [_rec(LINE, 0, len(LINE), "Вукьрадух")]
        guess = [_rec(LINE, 0, 14, "Вукьрадух")]

        report = score(gold, guess, resolver=_resolver())

        assert report.correct_chars == 14
        assert 0.3 < report.accuracy < 0.5

    def test_a_region_the_model_said_nothing_about_counts_against_it(self):
        gold = [_rec(LINE, 0, len(LINE), "Вукьрадух")]

        report = score(gold, [], resolver=_resolver())

        assert report.accuracy == 0.0
        assert report.covered_chars == len(LINE)

    def test_a_prediction_outside_the_gold_coverage_is_ignored(self):
        """The markup declines to say who owns the tail; a guess there is not wrong."""
        gold = [_rec(LINE, 0, 14, "Вукьрадух")]
        guess = [_rec(LINE, 0, len(LINE), "Вукьрадух")]

        report = score(gold, guess, resolver=_resolver())

        assert report.accuracy == 1.0
        assert report.covered_chars == 14

    def test_another_spelling_of_the_same_man_is_not_an_error(self):
        gold = [_rec(LINE, 0, len(LINE), "Куйбу Дегатти")]
        guess = [_rec(LINE, 0, len(LINE), "Дегатти")]

        report = score(gold, guess, resolver=_resolver())

        assert report.accuracy == 1.0

    def test_the_narrator_is_scored_like_any_other_speaker(self):
        prose = "Великан упал на колени."
        gold = [_rec(prose, 0, len(prose), "Рассказчик")]
        guess = [_rec(prose, 0, len(prose), "Рассказчик")]

        assert score(gold, guess, resolver=_resolver()).accuracy == 1.0


class TestPerRole:
    def test_precision_and_recall_are_reported_per_role(self):
        one, two = "– Раз.", "– Два."
        gold = [_rec(one, 0, len(one), "Вукьрадух"), _rec(two, 0, len(two), "Инвнехлизад", ordinal=1)]
        guess = [_rec(one, 0, len(one), "Вукьрадух"), _rec(two, 0, len(two), "Вукьрадух", ordinal=1)]

        report = score(gold, guess, resolver=_resolver())

        assert report.per_role["Вукьрадух"]["recall"] == 1.0
        assert report.per_role["Вукьрадух"]["precision"] == 0.5
        assert report.per_role["Инвнехлизад"]["recall"] == 0.0


class TestReporting:
    def test_disagreements_come_back_with_their_text(self):
        gold = [_rec(LINE, 0, len(LINE), "Вукьрадух")]
        guess = [_rec(LINE, 0, len(LINE), "Инвнехлизад")]

        report = score(gold, guess, resolver=_resolver())

        assert len(report.disagreements) == 1
        item = report.disagreements[0]
        assert item["expected"] == "Вукьрадух"
        assert item["predicted"] == "Инвнехлизад"
        assert "Здравствуй" in item["text"]

    def test_a_prediction_the_cast_does_not_know_is_counted_not_hidden(self):
        gold = [_rec(LINE, 0, len(LINE), "Вукьрадух")]
        guess = [_rec(LINE, 0, len(LINE), "Некто Безымянный")]

        report = score(gold, guess, resolver=_resolver())

        assert report.accuracy == 0.0
        assert report.unresolved_predictions["Некто Безымянный"] == 1

    def test_an_empty_run_is_not_an_accuracy_of_one(self):
        report = score([], [], resolver=_resolver())

        assert report.covered_chars == 0
        assert report.accuracy == 0.0
