"""Сохранённый подход и полное покрытие — разные доказательства."""
import pytest

from app.services.chapter_title_approaches import match_approaches
from app.services.chapter_title_timing import prepare_title_segments


def candidate(index, text):
    return {'chapter_id': f'ch{index}', 'chapter_index': index, 'lines': [f'Глава {index}. {text}']}


def speech(*texts):
    return [{'words': [{'word': word, 'start': n * 15 + i * .7,
                        'end': n * 15 + i * .7 + .2} for i, word in enumerate(text.split())]}
            for n, text in enumerate(texts)]


def test_full_repeat_without_number_and_later_full_stay_in_source_order():
    result = match_approaches([candidate(1, 'Тихая бухта')],
        speech('Глава первая Тихая бухта', 'Тихая бухта', 'Глава первая Тихая бухта'))
    assert [x['review_only'] for x in result['intervals']] == [False, True, False]
    assert [x['take_index'] for x in result['intervals']] == [1, 2, 3]
    assert not result['missing']


def test_typo_with_exact_number_is_kept_for_review_without_full_coverage():
    result = match_approaches([candidate(2, 'Серебряные ворота')],
        speech('Глава вторая Серебрянные ворота'))
    assert len(result['intervals']) == 1 and result['intervals'][0]['review_only']
    assert [x['chapter_id'] for x in result['missing']] == ['ch2']


@pytest.mark.parametrize('text', ['Глава третья Тихая бухта', 'Глава двадцать первая Тихая бухта',
                                  'Глава вторая Другая дорога'])
def test_wrong_number_and_unrelated_subtitle_are_not_assigned(text):
    result = match_approaches([candidate(2, 'Тихая бухта'), candidate(20, 'Тихая бухта')], speech(text))
    assert not result['intervals']


def test_expressive_vowels_and_annotations_do_not_drop_whole_approaches():
    result = match_approaches([candidate(1, 'Мы нашли дорогу')],
        speech('Главааа первая [смеясь] Мыыы нашли дорогу [тишина]'))
    assert len(result['intervals']) == 1
    assert result['intervals'][0]['start'] == 0
    assert result['intervals'][0]['end'] == pytest.approx(3.7)


def test_partial_repeat_never_proves_combined_heading_coverage():
    result = match_approaches([candidate(1, 'Тихая бухта')],
        speech('Глава первая посторонние слова', 'Тихая бухта'))
    assert len(result['intervals']) == 1 and result['intervals'][0]['review_only']
    assert result['missing']


def test_identical_subtitle_is_not_guessed_from_previous_number():
    result = match_approaches([candidate(1, 'Тихая бухта'), candidate(2, 'Тихая бухта')],
        speech('Глава первая Тихая бухта', 'Тихая бухта'))
    assert len(result['intervals']) == 1 and not result['intervals'][0]['review_only']


def test_zero_lexical_word_blocks_full_match_but_other_takes_survive():
    data = speech('Глава первая Тихая бухта', 'Глава первая Тихая бухта')
    word = data[0]['words'][2]
    word['end'] = word['start']
    prepared, issues = prepare_title_segments(data, 30)
    result = match_approaches([candidate(1, 'Тихая бухта')], prepared)
    assert issues and len(result['intervals']) == 1
    assert result['intervals'][0]['start'] == 15


def test_unrelated_long_speech_is_not_attached_to_a_prefix():
    result = match_approaches([candidate(1, 'Тихая бухта')],
        speech('Глава первая И вот мы поговорим о совершенно других вещах'))
    assert not result['intervals']



def test_english_numbered_title_retains_unprefixed_repeat_as_review():
    targets = [{'chapter_id': 'ch1', 'chapter_index': 1, 'lines': ['Chapter 1 Red Harbor']}]
    result = match_approaches(targets, speech('Chapter first Red Harbor', 'Red Harbor', 'Chapter one Red Harbor'), language='en')
    assert [entry['review_only'] for entry in result['intervals']] == [False, True, False]
    assert not result['missing']


def test_plain_heading_cannot_join_words_across_zero_lexical_barrier():
    data = [{'words': [{'word': 'Red', 'start': 0., 'end': .2},
                       {'word': 'unknown', 'start': .3, 'end': .3},
                       {'word': 'Harbor', 'start': 1., 'end': 1.2}]}]
    prepared, _issues = prepare_title_segments(data, 3)
    targets = [{'chapter_id': 'ch1', 'chapter_index': 1, 'lines': ['Red Harbor']}]
    assert not match_approaches(targets, prepared, language='en')['intervals']


def test_english_neighbor_context_for_dates_and_time_survives_preparation():
    from app.services.chapter_title_approaches import _stream
    from app.services.asr_align import word_stream
    data = speech('October 21 2026 at 2:05 pm')
    assert _stream(data, language='en') == word_stream(data, language='en')
