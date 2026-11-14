"""Нулевое слово отделяет соседей даже тогда, когда его нельзя превратить в клип."""
from copy import deepcopy

import pytest

from app.services.chapter_title_timing import prepare_title_segments


def word(text, start, end, **extra):
    return {"word": text, "start": start, "end": end, **extra}


def segments(*words):
    return [{"text": " ".join(item["word"] for item in words), "words": list(words)}]


def test_isolated_zero_punctuation_drops_without_retiming_positive_words():
    original = segments(word("Глава", 0, 1), word(".", 1, 1), word("первая", 1.5, 2))
    derived, issues = prepare_title_segments(original, 2)
    assert derived[0]["words"] == [original[0]["words"][0], original[0]["words"][2]]
    assert issues == []


def test_zero_lexical_word_stays_between_title_words_as_explicit_barrier():
    original = segments(word("Глава", 0, 1), word("оговорка", 1, 1), word("первая", 2, 3))
    derived, issues = prepare_title_segments(original, 3)
    assert derived[0]["words"] == [word("Глава", 0, 1), word("оговорка", 1, 1, _timing_unresolved=True),
                                    word("первая", 2, 3)]
    assert issues == [{"start": 1., "end": 1., "text": "оговорка", "reason": "invalid_word_timing", "candidates": []}]


def test_paid_cache_and_nested_metadata_are_not_mutated_or_shared():
    original = segments(word("первое", 0, 0, metadata={"confidence": [.8]}), word("слово", 1, 2))
    original[0]["metadata"] = {"source": ["paid-cache"]}
    snapshot = deepcopy(original)
    derived, issues = prepare_title_segments(original, 3)
    assert original == snapshot
    derived[0]["words"][0]["metadata"]["confidence"].append(.1)
    derived[0]["metadata"]["source"].append("derived")
    issues[0]["text"] = "изменено"
    assert original == snapshot


def test_zero_punctuation_only_segment_does_not_restore_segment_text_fallback():
    original = [{"text": "...", "start": 1, "end": 1, "words": [word("...", 1, 1)]}]
    assert prepare_title_segments(original, 2) == ([], [])


def test_zero_bracket_tag_remains_for_nonspeech_matcher_policy():
    original = segments(word("[noise]", 1, 1), word("Глава", 2, 3))
    derived, issues = prepare_title_segments(original, 4)
    assert derived == original and not issues


@pytest.mark.parametrize("text", ["42", "ё", "λ", "первая.", "слово-слово"])
def test_zero_lexical_tokens_cannot_be_dropped_as_punctuation(text):
    derived, issues = prepare_title_segments(segments(word(text, 1, 1)), 2)
    assert derived[0]["words"][0]["_timing_unresolved"] is True
    assert issues[0]["text"] == text


@pytest.mark.parametrize("start,end", [(-1, 1), (0, -1), (2, 1), (0, 3),
                                        (float("nan"), 1), (0, float("inf")),
                                        (True, 1), ("invalid", 1), (None, 1)])
def test_reversed_nonfinite_out_of_bounds_and_malformed_word_times_reject(start, end):
    with pytest.raises(ValueError, match="invalid_word_timing"):
        prepare_title_segments(segments(word("Глава", start, end)), 2)


@pytest.mark.parametrize("words", [
    [word("Глава", 1, 2), word("первая", .5, .5)],
    [word("Глава", 0, 2), word("первая", 1, 3)],
    [word("Глава", 0, 2), word(".", 1, 1)],
    [word("Глава", 0, 2), word("первая", 1, 1)],
    [word(".", 2, 2), word("Глава", 1, 2)],
])
def test_original_order_and_overlap_are_checked_before_removing_any_tokens(words):
    with pytest.raises(ValueError, match="invalid_word_timing"):
        prepare_title_segments(segments(*words), 4)


def test_order_is_validated_across_segments_and_zero_at_boundary_is_allowed():
    original = [*segments(word("Глава", 0, 1)), *segments(word("пропуск", 1, 1), word("первая", 1, 2))]
    derived, issues = prepare_title_segments(original, 2)
    assert len(derived) == 2 and len(issues) == 1
    with pytest.raises(ValueError, match="invalid_word_timing"):
        prepare_title_segments([*segments(word("Глава", 1, 2)), *segments(word("первая", .5, 1))], 2)


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan"), None, True])
def test_invalid_duration_rejects(duration):
    with pytest.raises(ValueError, match="invalid_word_timing"):
        prepare_title_segments([], duration)


@pytest.mark.parametrize("original", [None, {}, [None], [{"words": None}], [{"words": [None]}],
                                      [{"words": [{"word": "Глава", "start": 0}]}]])
def test_malformed_input_rejects_without_changing_the_cache(original):
    snapshot = deepcopy(original)
    with pytest.raises(ValueError, match="invalid_word_timing"):
        prepare_title_segments(original, 2)
    assert original == snapshot


def test_lexical_segment_without_word_times_never_uses_interpolated_matching():
    with pytest.raises(ValueError, match="missing_word_times"):
        prepare_title_segments([{"text": "Глава первая", "start": 0, "end": 2}], 2)


def test_invalid_segment_bounds_also_reject_when_word_times_are_valid():
    original = [{"text": "Глава", "start": 2, "end": 1, "words": [word("Глава", 0, 1)]}]
    with pytest.raises(ValueError, match="invalid_word_timing"):
        prepare_title_segments(original, 3)


def test_container_rounding_does_not_reject_valid_word_times():
    original = [{'text': 'Глава', 'start': 0, 'end': 3.0000625,
                 'words': [word('Глава', 0, 2.9)]}]
    derived, issues = prepare_title_segments(original, 3.)
    assert derived == original and not issues
    original[0]['words'][0]['end'] = 3.0000625
    with pytest.raises(ValueError, match='invalid_word_timing'):
        prepare_title_segments(original, 3.)
