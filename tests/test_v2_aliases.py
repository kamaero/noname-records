"""Matching a role name written one way against the cast that writes it another.

The benchmark must not punish a model for saying «Дегатти» where the cast says «Куйбу
Дегатти». Four rules, each accepted separately by the owner:

  * an explicit merge list, for what only a person can decide;
  * drop a parenthetical: «Коруак (управляющий Банка Душ)» is «Коруак»;
  * drop a «Рассказчик» prefix: «Рассказчик Инвнехлизад» is a tale Инвнехлизад tells;
  * accept a containment only when exactly one cast signature contains the name.

The last one is deliberately narrow. «Дегатти» sits inside both «Куйбу Дегатти» and
«Мэтр Дегатти», so it resolves to neither by machine — which is why the owner named
that family himself.
"""
from app.v2.aliases import DEGATTI, AliasResolver


CAST = {
    "куйбу дегатти": "Куйбу Дегатти",
    "мэтр дегатти": "Мэтр Дегатти",
    "коруак": "Коруак",
    "инвнехлизад": "Инвнехлизад",
    "кайкелона чу": "Кайкелона Чу",
    "вукьрадух": "Вукьрадух",
    "кот": "Кот",
}


def _resolver():
    return AliasResolver(cast=CAST, merges=[DEGATTI])


class TestExplicitMerges:
    def test_every_spelling_of_degatti_lands_on_one_role(self):
        resolver = _resolver()

        assert {resolver.resolve(name) for name in ("Дегатти", "Мэтр Дегатти", "Куйбу Дегатти", "Куйбу")} == {
            "Куйбу Дегатти"
        }

    def test_a_pair_the_owner_declined_stays_apart(self):
        """«Дегатти в интерлюдии» was not in the list, and containment cannot save it:
        no cast signature contains that whole phrase."""
        assert _resolver().resolve("Дегатти в интерлюдии") is None

    def test_the_owner_declined_the_snowball_cat_too(self):
        resolver = AliasResolver(cast={**CAST, "кот снежок": "Кот Снежок"}, merges=[DEGATTI])

        assert resolver.resolve("Кот") == "Кот"
        assert resolver.resolve("Кот Снежок") == "Кот Снежок"


class TestRules:
    def test_a_name_already_in_the_cast_needs_no_rule(self):
        assert _resolver().resolve("Вукьрадух") == "Вукьрадух"

    def test_folding_ignores_a_stress_mark(self):
        assert _resolver().resolve("Вукьраду́х") == "Вукьрадух"

    def test_a_parenthetical_is_dropped(self):
        assert _resolver().resolve("Коруак (управляющий Банка Душ)") == "Коруак"

    def test_the_narrator_prefix_is_dropped(self):
        assert _resolver().resolve("Рассказчик Инвнехлизад") == "Инвнехлизад"

    def test_a_containment_matching_exactly_one_signature_is_accepted(self):
        assert _resolver().resolve("Кайкелона") == "Кайкелона Чу"

    def test_a_containment_matching_two_signatures_is_refused(self):
        """Without the owner's merge list «Дегатти» is genuinely ambiguous."""
        bare = AliasResolver(cast=CAST, merges=[])

        assert bare.resolve("Дегатти") is None

    def test_an_unknown_name_resolves_to_nothing(self):
        assert _resolver().resolve("Некто Безымянный") is None

    def test_the_narrator_is_not_a_cast_member(self):
        """He is a role in the script and belongs to no actor's list."""
        assert _resolver().resolve("Рассказчик") == "Рассказчик"

    def test_nothing_resolves_to_nothing(self):
        assert _resolver().resolve("") is None
        assert _resolver().resolve(None) is None


class TestBookkeeping:
    def test_the_resolver_reports_what_it_could_not_place(self):
        resolver = _resolver()
        for name in ("Вукьрадух", "Некто", "Некто", "Другой"):
            resolver.resolve(name)

        assert resolver.unresolved == {"Некто": 2, "Другой": 1}
