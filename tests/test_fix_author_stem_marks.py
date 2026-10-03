"""Ремонт меток, которые старое правило окончаний перенесло на чужое слово."""
from scripts.v2.fix_author_stem_marks import replacement, stem_collateral

LAYER = {"матери": 1, "банке": 1, "игуменье": 4, "полумрак": 6, "хилакток": 6}
KNOWN = {"материал": 6, "банкир": 4, "банкнот": 5, "игуменья": 2}.get


def test_another_word_with_the_same_start_is_collateral():
    assert stem_collateral("материал", 1, LAYER, known=KNOWN) == "матери"
    assert stem_collateral("банкир", 1, LAYER, known=KNOWN) == "банке"
    assert stem_collateral("банкнот", 1, LAYER, known=KNOWN) == "банке"


def test_a_real_form_of_an_author_word_is_left_alone():
    assert stem_collateral("игуменья", 4, LAYER, known=KNOWN) is None
    assert stem_collateral("полумрака", 6, LAYER, known=KNOWN) is None
    assert stem_collateral("матери", 1, LAYER, known=KNOWN) is None
    assert stem_collateral("хилактокка", 6, LAYER, known=KNOWN) is None, "производное выдуманного слова"


def test_a_mark_placed_elsewhere_is_an_operator_decision_not_the_rule():
    """Ударение не там, куда его перенесло бы правило, — его ставил человек."""
    assert stem_collateral("материал", 6, LAYER, known=KNOWN) is None


def test_the_replacement_comes_from_a_dictionary_with_one_reading():
    lookups = [("dict", {"банкир": 4}.get), ("wiktionary", {"банкнот": 5, "замки": [1, 4]}.get)]
    assert replacement("банкир", lookups) == (4, "dict")
    assert replacement("банкнот", lookups) == (5, "wiktionary")
    assert replacement("замки", lookups) is None
    assert replacement("неведомо", lookups) is None
