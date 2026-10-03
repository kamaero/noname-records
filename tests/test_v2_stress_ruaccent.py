"""Aligning RUAccent's `+`-marked output back onto the original text.

RUAccent is faked here: what matters is that its habits — dropped punctuation, glued
apostrophe words, restored «ё», no `+` on «ё» — do not move a single mark onto the
wrong word. The library itself is optional and must stay absent from the test venv.
"""
import sys

import pytest

import app.v2.stress_ruaccent as ruaccent_layer
from app.v2.stress import Resolver, find_words, stress_segment
from app.v2.stress_ruaccent import align_plus_marks, context_stress, get_ruaccent


def words(text):
    return [text[s:e] for s, e in find_words(text)]


def test_plus_before_the_vowel_becomes_an_offset_into_the_source_word():
    text = "Замок на двери – это замок?"
    marked = "З+амок на двер+и  +это зам+ок?"
    got = align_plus_marks(text, marked)
    assert words(text) == ["Замок", "двери", "это", "замок"]
    assert got == {0: 1, 1: 4, 2: 0, 3: 3}


def test_glued_apostrophe_and_non_breaking_hyphen_words_are_split_back():
    text = "Уль’Тахан и Йог‑Сотхотх вошли в Полумрак."
    marked = "УльТах+ан и ЙогСотх+отх вошл+и в Полумр+ак."
    got = align_plus_marks(text, marked)
    assert words(text) == ["Тахан", "Сотхотх", "вошли", "Полумрак"]
    assert got == {0: 3, 1: 4, 2: 4, 3: 6}


def test_restored_yo_still_aligns_and_yo_words_get_no_answer():
    text = "Ещё ежик пришел"
    marked = "Ещё ёжик приш+ел"
    got = align_plus_marks(text, marked)
    # «Ещё» is the rule's business; «ёжик» has no «+» so RUAccent says nothing about it.
    assert got == {2: 4}


def test_a_dropped_word_does_not_shift_the_rest():
    text = "Стало много, сказал Дгарнин"
    marked = "Ст+ало сказ+ал Дг+арнин"
    assert align_plus_marks(text, marked) == {0: 2, 2: 4, 3: 2}


def test_plus_on_a_consonant_or_out_of_range_is_ignored():
    assert align_plus_marks("Стало", "С+тало") == {}
    assert align_plus_marks("Стало", "Стало+") == {}


def test_a_word_already_marked_in_the_source_is_left_to_the_text_layer():
    assert align_plus_marks("Дгарни́н сидел", "Дг+арнин сид+ел") == {1: 3}


def test_context_stress_returns_none_without_the_library(monkeypatch):
    monkeypatch.setitem(sys.modules, "ruaccent", None)
    monkeypatch.setattr(ruaccent_layer, "_INSTANCE", None)
    monkeypatch.setattr(ruaccent_layer, "_UNAVAILABLE", False)
    assert get_ruaccent() is None
    assert context_stress("Стало много") is None
    assert ruaccent_layer.context_layer() is None


def test_context_stress_with_a_fake_model_feeds_the_resolver():
    class Fake:
        def process_all(self, text):
            return "Ст+ало зам+ок"

    def layer(text):
        return context_stress(text, accent=Fake())

    resolver = Resolver(dict_lookup=lambda w: {"замок": [1, 3]}.get(w), context=layer)
    marks, report = stress_segment("Стало замок", resolver=resolver)
    assert [(m.vowel_offset, m.source) for m in marks] == [(2, "context"), (3, "context")]
    assert report["counts"]["context"] == 2


def test_tokenizer_wrapper_adds_token_type_ids():
    np = pytest.importorskip("numpy")

    class Encoding(dict):
        pass

    def inner(word, return_tensors=None):
        return Encoding(input_ids=np.array([[1, 2, 3]]))

    wrapped = ruaccent_layer._TokenizerWithTypeIds(inner)
    encoded = wrapped("слово", return_tensors="np")
    assert encoded["token_type_ids"].tolist() == [[0, 0, 0]]

    class Model:
        pass

    class Accent:
        pass

    accent = Accent()
    accent.accent_model = Model()
    accent.accent_model.tokenizer = inner
    ruaccent_layer._patch_token_type_ids(accent)
    ruaccent_layer._patch_token_type_ids(accent)  # idempotent
    assert isinstance(accent.accent_model.tokenizer, ruaccent_layer._TokenizerWithTypeIds)
    assert accent.accent_model.tokenizer._inner is inner
