"""«Только редкие» ударения: знак нужен там, где чтец в потоке ошибается.

Рассказчик «Полумракских баек» говорил карли́ца, факело́в, сомнамбу́ла — не потому что
ударения не было, а потому что читал подряд. Над «или», «даже», «через» знак ему не
нужен, а среди двухсот тысяч знаков на книгу редкое слово теряется. Редким считается
слово, которое в живом языке встречается реже раза на миллион слов (wordfreq, zipf < 3).
"""
from types import SimpleNamespace

from app.v2.reader import segments_payload
from app.v2.word_rarity import is_rare


def test_everyday_words_are_not_rare():
    for word in ("или", "даже", "через", "такая", "голос", "Сказал"):
        assert not is_rare(word), word


def test_the_words_the_narrator_got_wrong_are_rare():
    for word in ("карлица", "карлицу", "факелов", "сомнамбула", "толику", "Бимькмолепус"):
        assert is_rare(word), word


def test_yo_and_ye_spellings_are_the_same_word():
    """Книга печатает «ее» за «её» — частое слово не становится редким от пропущенной точки."""
    assert not is_rare("ее")
    assert not is_rare("её")
    assert not is_rare("еще")


def test_the_reader_payload_says_which_marks_are_on_rare_words():
    text = "Он добавил толику яда."
    segment = SimpleNamespace(id="s1", ordinal=0, kind="paragraph", text=text)
    marks = [SimpleNamespace(word_start=3, word_end=10, vowel_offset=4, source="context"),   # добавил
             SimpleNamespace(word_start=11, word_end=17, vowel_offset=3, source="wiktionary")]  # толику
    payload = segments_payload([segment], {}, {"s1": marks})
    assert [(m["start"], m["rare"]) for m in payload[0]["stress"]] == [(3, False), (11, True)]
