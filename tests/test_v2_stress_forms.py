"""The word-form layer: every inflected form Wiktionary knows, between the dictionaries and RUAccent.

The base dictionary holds headwords — «толи́ка», not «толику». An inflected form fell
through to RUAccent, which settles it by context and so settles it differently in
different sentences: «толи́ку яда», but «то́лику терпения» two lines later. The actor
recording Пупип heard the second one. A word with one reading should never reach a
model that guesses; this layer is the one that knows the forms.
"""
import json

from app.v2.stress import Resolver, stress_segment
from app.v2.stress_forms import build_forms_db, forms_from_entry, open_lookup

TOLIKA = {
    "word": "толика",
    "pos": "noun",
    "forms": [
        {"form": "толи́ка", "tags": ["canonical", "feminine", "inanimate"]},
        {"form": "tolíka", "tags": ["romanization"]},
        {"form": "ru-noun-table", "source": "declension", "tags": ["inflection-template"]},
        {"form": "accent-a", "source": "declension", "tags": ["class"]},
        {"form": "толи́ку", "tags": ["accusative", "singular"], "source": "declension"},
        {"form": "толи́ки", "tags": ["genitive", "singular"], "source": "declension"},
        {"form": "толи́кою", "tags": ["instrumental", "singular"], "source": "declension"},
    ],
}


def test_an_entry_gives_every_stressed_form_it_lists():
    got = dict(forms_from_entry(TOLIKA))
    assert got == {"толика": {3}, "толику": {3}, "толики": {3}, "толикою": {3}}


def test_obsolete_and_nonstandard_forms_do_not_count():
    """A dated variant with another stress would turn a plain word into a false homograph."""
    entry = {"word": "звонить", "forms": [
        {"form": "звони́т", "tags": ["present", "third-person", "singular"]},
        {"form": "зво́нит", "tags": ["present", "third-person", "singular", "nonstandard"]},
        {"form": "звони́тъ", "tags": ["pre-reform"]},
    ]}
    assert dict(forms_from_entry(entry)) == {"звонит": {4}}


def test_a_form_with_yo_and_no_acute_is_stressed_on_yo():
    entry = {"word": "берёза", "forms": [{"form": "берёзу", "tags": ["accusative"]}]}
    assert dict(forms_from_entry(entry)) == {"берёзу": {3}}


def test_multiword_latin_and_one_vowel_forms_are_skipped():
    entry = {"word": "читать", "forms": [
        {"form": "бу́ду чита́ть", "tags": ["future"]},
        {"form": "chitátʹ", "tags": ["romanization"]},
        {"form": "чта́", "tags": ["x"]},
    ]}
    assert dict(forms_from_entry(entry)) == {}


def _build(tmp_path, entries):
    source = tmp_path / "kaikki.jsonl"
    source.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries), encoding="utf-8")
    target = tmp_path / "forms.sqlite"
    stats = build_forms_db(source, target)
    return open_lookup(target), stats


def test_two_lemmas_with_different_stress_make_a_homograph(tmp_path):
    lookup, _ = _build(tmp_path, [
        {"word": "замок", "forms": [{"form": "за́мок", "tags": ["canonical"]}]},
        {"word": "замок", "forms": [{"form": "замо́к", "tags": ["canonical"]}]},
        TOLIKA,
    ])
    assert lookup("замок") == [1, 3]
    assert lookup("толику") == 3
    assert lookup("нетслова") is None


def test_yo_is_also_found_under_ye_and_a_clash_makes_a_homograph(tmp_path):
    """Books print «еще» for «ещё». Where the «е» spelling is a word of its own —
    «мо́ем» from мыть — a book without «ё» may mean either, so the sentence decides."""
    lookup, _ = _build(tmp_path, [
        {"word": "ещё", "forms": [{"form": "ещё", "tags": ["canonical"]}]},
        {"word": "мой", "forms": [{"form": "моём", "tags": ["prepositional"]}]},
        {"word": "мыть", "forms": [{"form": "мо́ем", "tags": ["present", "first-person", "plural"]}]},
    ])
    assert lookup("еще") == 2
    assert lookup("моем") == [1, 2]
    assert lookup("моём") == 2


def test_a_name_does_not_make_a_common_word_a_homograph(tmp_path):
    """«То́лику» is the dative of Толик; «толи́ку» is what the text meant. A name only
    counts where no common word has that form — «Москву» is still found."""
    lookup, _ = _build(tmp_path, [
        TOLIKA,
        {"word": "Толик", "pos": "name", "forms": [{"form": "То́лику", "tags": ["dative"]}]},
        {"word": "Москва", "pos": "name", "forms": [{"form": "Москву́", "tags": ["accusative"]}]},
    ])
    assert lookup("толику") == 3
    assert lookup("москву") == 5


def test_the_file_missing_means_the_layer_is_off(tmp_path):
    assert open_lookup(tmp_path / "nope.sqlite")("толику") is None


def test_the_forms_layer_answers_only_what_the_dictionaries_left():
    forms = {"толику": 3, "голос": 3}.get
    resolver = Resolver(dict_lookup={"голос": 1}.get, forms_lookup=forms)
    text = "Он добавил толику яда в голос."
    marks, report = stress_segment(text, resolver=resolver)
    by_word = {text[m.word_start:m.word_end]: (m.vowel_offset, m.source) for m in marks}
    assert by_word["толику"] == (3, "wiktionary")
    assert by_word["голос"] == (1, "dict"), "a dictionary answer is never overridden"
    assert report["counts"]["wiktionary"] == 1


def test_the_forms_layer_comes_before_the_context_model():
    """The bug itself: RUAccent must not get a word Wiktionary already knows."""
    asked = []

    def context(text):
        asked.append(text)
        return {0: 1, 1: 1}  # «то́лику», and a guess for the other word

    resolver = Resolver(forms_lookup={"толику": 3}.get, context=context)
    text = "толику терпения"
    marks, _ = stress_segment(text, resolver=resolver)
    by_word = {text[m.word_start:m.word_end]: (m.vowel_offset, m.source) for m in marks}
    assert by_word["толику"] == (3, "wiktionary")
    assert by_word["терпения"][1] == "context"


def test_a_homograph_in_the_forms_layer_goes_to_context_with_its_options():
    resolver = Resolver(forms_lookup={"стены": [2, 4]}.get, context=lambda text: {0: 4})
    marks, report = stress_segment("стены", resolver=resolver)
    assert report["homographs"] == ["стены"]
    assert (marks[0].vowel_offset, marks[0].source) == (4, "context")


def test_the_author_still_wins():
    resolver = Resolver(author={"бимькмолепус": 10}, forms_lookup=lambda w: 1)
    marks, _ = stress_segment("Бимькмолепус", resolver=resolver)
    assert (marks[0].vowel_offset, marks[0].source) == (10, "author")
