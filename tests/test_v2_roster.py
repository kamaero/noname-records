"""The cycle's cast, gathered from the legends of every marked-up chapter.

Colours cannot identify a character across the cycle: they are reused between tales,
and measured over all 67 files only the recurring leads keep one — «H:magenta» is
Вукьрадух in 92% of chapters, but «S:d9ead3» takes a different role almost every time.
The actor can identify one. An actor's name in a legend was typed by the owner and
does not travel from role to role.

So the roster is keyed by role name, corroborated by the actor, weighted by how many
chapters the role appears in. Roles that recur with one settled actor are the owner's
own reference and belong in the author profile instead of being re-derived by a model
for every book.
"""
from app.v2.gdoc_import import LegendEntry
from app.v2.roster import build_roster, merge_candidates, settled_roles


class _Chapter:
    def __init__(self, title, legend):
        self.title = title
        self.path = f"{title}.docx"
        self.legend = legend


def _ch(title, pairs):
    return _Chapter(title, {f"C{i}": LegendEntry(role=r, actor=a) for i, (r, a) in enumerate(pairs)})


def test_a_role_seen_in_several_chapters_carries_them_all():
    roster = build_roster([
        _ch("Байка 3", [("Вукьрадух", "Глеб Лапин")]),
        _ch("Байка 22", [("Вукьрадух", "Глеб Лапин")]),
    ])

    entry = next(iter(roster.values()))
    assert entry.actor == "Глеб Лапин"
    assert len(entry.chapters) == 2


def test_a_stress_mark_does_not_split_one_role_in_two():
    """«Дгарни́н» and «Дгарнин» are one demon; the fold is the profile's own."""
    roster = build_roster([
        _ch("Байка 1", [("Дгарни́н", "Актёр А")]),
        _ch("Байка 2", [("Дгарнин", "Актёр А")]),
    ])

    assert len(roster) == 1
    assert len(next(iter(roster.values())).chapters) == 2


def test_two_spellings_of_one_man_stay_apart_until_someone_says_otherwise():
    """Legends carry «Дегатти», «Куйбу Дегатти» and «Мэтр Дегатти» for one man.

    A machine must not decide that. «Хилакток 2» and «Хилакток 3» sit in the same
    relation to each other and are two different demons, so merging by containment
    would quietly fuse distinct roles. The roster reports what the legends say.
    """
    roster = build_roster([
        _ch("Байка 1", [("Дегатти", "Роман Сомов")]),
        _ch("Байка 2", [("Куйбу Дегатти", "Роман Сомов")]),
    ])

    assert len(roster) == 2


def test_the_same_actor_under_two_names_is_offered_for_merging():
    """The owner asked for reference only where certainty is total.

    Same actor plus one name inside the other is a strong hint and a weak proof, so
    it comes back as a question rather than a decision.
    """
    roster = build_roster([
        _ch("Байка 1", [("Дегатти", "Роман Сомов")]),
        _ch("Байка 2", [("Куйбу Дегатти", "Роман Сомов")]),
        _ch("Байка 3", [("Мэтр Дегатти", "Роман Сомов")]),
    ])

    pairs = merge_candidates(roster)

    assert ("Дегатти", "Куйбу Дегатти") in [tuple(sorted(p, key=len)) for p in pairs]
    assert all(len(set(p)) == 2 for p in pairs)


def test_different_roles_that_merely_share_a_prefix_are_not_offered():
    """«Хилакток 2» and «Хилакток 3»: neither contains the other, and the actors differ."""
    roster = build_roster([
        _ch("Байка 1", [("Хилакток 2", "Актёр А")]),
        _ch("Байка 2", [("Хилакток 3", "Актёр Б")]),
    ])

    assert merge_candidates(roster) == []


def test_two_actors_for_one_role_is_not_settled():
    """A role the legends disagree about is not reference material."""
    roster = build_roster([
        _ch("Байка 1", [("Дазиптовд", "Ольга Зубкова")]),
        _ch("Байка 2", [("Дазиптовд", "Другая Актриса")]),
    ])

    entry = next(iter(roster.values()))
    assert entry.actor_is_settled is False
    assert settled_roles(roster) == []


def test_a_legend_that_named_no_actor_does_not_unsettle_the_one_that_did():
    """Some legends omit the actor — the owner said so. Silence is not disagreement."""
    roster = build_roster([
        _ch("Байка 1", [("Вукьрадух", "Глеб Лапин")]),
        _ch("Байка 2", [("Вукьрадух", "")]),
    ])

    entry = next(iter(roster.values()))
    assert entry.actor == "Глеб Лапин"
    assert entry.actor_is_settled is True


def test_a_walk_on_from_a_single_tale_is_not_reference():
    roster = build_roster([_ch("Байка 1", [("Дружинник", "Константин Румянцев")])])

    assert settled_roles(roster) == []
    assert len(settled_roles(roster, min_chapters=1)) == 1


def test_a_question_mark_is_not_an_actor():
    """The legends carry «Роль - ?» where nobody is cast yet.

    Taken literally it passes for a settled actor and the role reaches the reference
    list — nine of the first twenty-five did, on the real corpus — so a role whose
    casting is still open would be presented as decided.
    """
    roster = build_roster([
        _ch("Байка 1", [("Вохпкогамкиуб", "?")]),
        _ch("Байка 2", [("Вохпкогамкиуб", "?")]),
    ])

    entry = next(iter(roster.values()))
    assert entry.actor == ""
    assert settled_roles(roster) == []


def test_a_question_mark_beside_a_real_name_does_not_dispute_it():
    roster = build_roster([
        _ch("Байка 1", [("Бимькмолепус", "Пётр Ступников")]),
        _ch("Байка 2", [("Бимькмолепус", "?")]),
    ])

    entry = next(iter(roster.values()))
    assert entry.actor == "Пётр Ступников"
    assert entry.actor_is_settled is True


def test_an_actor_name_carrying_a_query_is_still_a_name():
    """«Роман Сомов (?)» is a person the owner is unsure about, not an empty slot."""
    roster = build_roster([
        _ch("Байка 1", [("Гсогмпивьхпаб", "Роман Сомов (?)")]),
        _ch("Байка 2", [("Гсогмпивьхпаб", "Роман Сомов (?)")]),
    ])

    assert next(iter(roster.values())).actor == "Роман Сомов (?)"


def test_a_role_with_no_actor_anywhere_is_not_reference():
    roster = build_roster([
        _ch("Байка 1", [("Банкир Субиух", "")]),
        _ch("Байка 2", [("Банкир Субиух", "")]),
    ])

    assert settled_roles(roster) == []


def test_reference_roles_come_back_most_frequent_first():
    roster = build_roster([
        _ch("Байка 1", [("Вукьрадух", "Глеб Лапин"), ("Погтда", "Актриса Л")]),
        _ch("Байка 2", [("Вукьрадух", "Глеб Лапин"), ("Погтда", "Актриса Л")]),
        _ch("Байка 3", [("Вукьрадух", "Глеб Лапин")]),
    ])

    assert [e.display_role for e in settled_roles(roster)] == ["Вукьрадух", "Погтда"]


def test_an_empty_role_is_ignored():
    roster = build_roster([_ch("Байка 1", [("", "Актёр"), ("   ", "Актёр")])])

    assert roster == {}
