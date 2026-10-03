"""Stress marks as positions over unchanged text.

The old pipeline wrote U+0301 into the prose; v2 keeps the prose clean and records
(word span, vowel offset, source). The sources form a chain with a fixed order — what
the author said beats what the dictionary says, which beats what a context model
guesses — and the report has to say which layer answered for each word, because
that is what tells a human where the remaining errors can come from.
"""
from app.v2.stress import (
    Resolver,
    StressMark,
    find_words,
    offset_from_stressed_form,
    render_marks,
    stress_segment,
)


def spans(text):
    return [text[s:e] for s, e in find_words(text)]


def test_find_words_skips_single_vowel_words_and_non_cyrillic():
    text = "Нас стало слишком много, — сказал Dgarnin в 1999 году."
    assert spans(text) == ["стало", "слишком", "много", "сказал", "году"]


def test_find_words_splits_hyphenated_compounds_into_parts():
    assert spans("кто-нибудь придёт") == ["нибудь", "придёт"]


def test_find_words_keeps_a_word_already_carrying_a_mark_as_one_span():
    text = "Дгарни́н сидел"
    (s, e), _ = find_words(text)
    assert text[s:e] == "Дгарни́н"


def test_offset_from_stressed_form_by_character_and_by_vowel_count():
    assert offset_from_stressed_form("замок", "за́мок") == 1
    assert offset_from_stressed_form("замок", "замо́к") == 3
    # Secondary stress (U+0300) in the dictionary form is not the answer.
    assert offset_from_stressed_form("автомассажер", "а̀втомассажёр") == 10
    assert offset_from_stressed_form("замок", "замок") is None


def _marks(text, resolver):
    marks, report = stress_segment(text, resolver=resolver)
    return [(text[m.word_start:m.word_end], m.vowel_offset, m.source) for m in marks], report


def test_author_beats_dictionary_and_capitalised_forms_resolve_via_lowercase():
    resolver = Resolver(
        author={"полумрак": 6},
        dict_lookup=lambda w: {"полумрак": 1}.get(w),
    )
    got, report = _marks("Полумрак уснул", resolver)
    assert got == [("Полумрак", 6, "author")]
    assert report["counts"]["author"] == 1
    assert report["unresolved"] == ["уснул"]


def test_yo_is_always_stressed_and_wins_over_the_dictionary():
    resolver = Resolver(dict_lookup=lambda w: {"ещё": 0}.get(w))
    got, _ = _marks("Ещё придёт", resolver)
    assert got == [("Ещё", 2, "rule"), ("придёт", 4, "rule")]


def test_dictionary_unambiguous_form_is_used_and_homograph_is_deferred_to_context():
    seen = []

    def context(text):
        seen.append(text)
        return {1: 1}  # second word «замок» → за́мок

    resolver = Resolver(
        dict_lookup=lambda w: {"стало": 2, "замок": [1, 3]}.get(w),
        context=context,
    )
    got, report = _marks("Стало замок", resolver)
    assert got == [("Стало", 2, "dict"), ("замок", 1, "context")]
    assert seen == ["Стало замок"]
    assert report["counts"] == {"text": 0, "author": 0, "rule": 0, "dict": 1, "wiktionary": 0, "context": 1, "residue": 0, "unresolved": 0}
    assert report["homographs"] == ["замок"]


def test_context_answer_outside_the_homograph_options_is_kept_with_low_confidence():
    # «молоко»: the dictionary allows the 2nd and 3rd «о»; context insists on the 1st.
    resolver = Resolver(dict_lookup=lambda w: [3, 5], context=lambda text: {0: 1})
    marks, _ = stress_segment("молоко", resolver=resolver)
    assert (marks[0].vowel_offset, marks[0].source) == (1, "context")
    assert marks[0].confidence < 0.6


def test_context_is_only_called_when_something_is_left_for_it():
    calls = []
    resolver = Resolver(dict_lookup=lambda w: 2, context=lambda text: calls.append(text) or {})
    stress_segment("Стало много", resolver=resolver)
    assert calls == []


def test_out_of_dictionary_word_goes_to_context_then_residue():
    residue_seen = []

    def residue(words):
        residue_seen.append([w for _, w in words])
        return {i: 7 for i, _ in words}

    resolver = Resolver(dict_lookup=lambda w: None, context=lambda text: {0: 1}, residue=residue)
    got, report = _marks("Бимькмолепус Вукьрадух", resolver)
    assert got == [("Бимькмолепус", 1, "context"), ("Вукьрадух", 7, "residue")]
    assert residue_seen == [["Вукьрадух"]]
    assert report["unresolved"] == []


def test_mark_already_in_the_text_is_respected():
    resolver = Resolver(dict_lookup=lambda w: 3)
    got, report = _marks("Дгарни́н сидел", resolver)
    assert got == [("Дгарни́н", 5, "text"), ("сидел", 3, "dict")]
    assert report["counts"]["text"] == 1


def test_hyphenated_compound_is_looked_up_whole_when_the_part_is_unknown():
    resolver = Resolver(dict_lookup=lambda w: {"кто-нибудь": 7}.get(w))
    got, report = _marks("кто-нибудь", resolver)
    # offset 7 in «кто-нибудь» is the «у» of «нибудь» → offset 3 inside the part
    assert got == [("нибудь", 3, "dict")]
    assert report["unresolved"] == []


def test_compound_whose_stress_falls_in_another_part_is_resolved_without_a_mark():
    resolver = Resolver(dict_lookup=lambda w: {"кое-какой": 1}.get(w))
    got, report = _marks("кое-какой", resolver)
    assert got == [("кое", 1, "dict")]
    assert report["unresolved"] == []


def test_context_result_pointing_at_a_consonant_is_ignored():
    resolver = Resolver(dict_lookup=lambda w: None, context=lambda text: {0: 1})
    got, report = _marks("Стало", resolver)
    assert got == []
    assert report["unresolved"] == ["Стало"]


def test_render_marks_writes_the_acute_after_the_vowel():
    text = "Стало много"
    marks = [StressMark(0, 5, 2, "dict"), StressMark(6, 11, 2, "dict")]
    assert render_marks(text, marks) == "Ста́ло мно́го"


def test_render_marks_does_not_double_a_mark_already_in_the_text():
    text = "Дгарни́н"
    marks = [StressMark(0, 8, 5, "text")]
    assert render_marks(text, marks) == "Дгарни́н"


def test_default_resolver_reads_author_map_from_pronunciation_rows():
    class Row:
        def __init__(self, term, stressed):
            self.term, self.stressed = term, stressed

    resolver = Resolver.from_author_rows([Row("Полумрак", "полумра́к"), Row("x", "")], dict_lookup=lambda w: None)
    got, _ = _marks("Полумрак", resolver)
    assert got == [("Полумрак", 6, "author")]


def test_author_stress_carries_over_to_inflected_forms_with_lower_confidence():
    resolver = Resolver(author={"полумрак": 6, "погтда": 1, "краа": 2})
    marks, report = stress_segment("Полумрака Погтду Погтда краами", resolver=resolver)
    got = [(m.vowel_offset, m.source, m.confidence) for m in marks]
    assert got == [(6, "author", 0.9), (1, "author", 0.9), (1, "author", 1.0)]
    # «краами»: the stem «кра» is too short to be trusted as a name.
    assert report["unresolved"] == ["краами"]


def test_author_stem_does_not_swallow_a_long_unrelated_ending():
    resolver = Resolver(author={"полумрак": 6})
    marks, _ = stress_segment("полумракский", resolver=resolver)
    assert marks == []


def test_default_dict_lookup_treats_either_dictionary_saying_homograph_as_a_homograph(monkeypatch):
    import app.pronunciation as pronunciation
    from app.v2 import stress_dict as base_dict
    from app.v2.stress import default_dict_lookup

    monkeypatch.setattr(pronunciation, "load_pronunciation_dict", lambda: {"двери": "две́ри", "много": "мно́го", "агент": ["а́гент", "аге́нт"]})
    monkeypatch.setattr(base_dict, "lookup", lambda w: {"двери": ["две́ри", "двери́"], "сидел": "сиде́л", "агент": None}.get(w))
    assert default_dict_lookup("двери") == [2, 4]
    assert default_dict_lookup("агент") == [0, 2]
    assert default_dict_lookup("много") == 2
    assert default_dict_lookup("сидел") == 3
    assert default_dict_lookup("стало") is None


def test_an_author_stem_takes_real_endings_not_the_start_of_another_word():
    """«матери=Ма́тери» дало «ма́териал», «Банке=Ба́нке» — «ба́нкир», «ба́нкет», «ба́нкнот»:
    основа «матер»/«банк» плюс любые три буквы. Форма слова автора — это основа плюс
    падежное окончание; «-иал», «-ир», «-ет», «-нот» — начало другого слова."""
    known = {"материал": 6, "банкир": 4, "банкет": 4, "банкнот": 5}
    resolver = Resolver(author={"матери": 1, "банке": 1}, dict_lookup=known.get)
    marks, _ = stress_segment("материал банкир банкет банкнот банку матерью", resolver=resolver)
    got = [(m.vowel_offset, m.source) for m in marks]
    assert got == [(6, "dict"), (4, "dict"), (4, "dict"), (5, "dict"), (1, "author"), (1, "author")]


def test_the_author_still_overrides_the_dictionary_on_forms_of_his_own_word():
    """«Игуме́нье» у автора — не по словарю, и нарочно: его форма «игуме́нья» остаётся за ним."""
    resolver = Resolver(author={"игуменье": 4}, dict_lookup={"игуменья": 2}.get)
    marks, _ = stress_segment("игуменья игуменьи", resolver=resolver)
    assert [(m.vowel_offset, m.source) for m in marks] == [(4, "author"), (4, "author")]


def test_a_derivative_of_an_invented_word_keeps_the_author_stress():
    """«хилакто́кка», «полумра́кцы»: словарям они неизвестны — переносить ударение автора
    на них верно, как и раньше."""
    resolver = Resolver(author={"хилакток": 6, "полумрак": 6}, dict_lookup=lambda w: None)
    marks, _ = stress_segment("хилактокка полумракцы", resolver=resolver)
    assert [(m.vowel_offset, m.source) for m in marks] == [(6, "author"), (6, "author")]
