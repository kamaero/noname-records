"""The cycle's cast, gathered from the legends of every marked-up chapter.

Colours cannot identify a character across the cycle: measured over all 67 files they
are reused between tales, and only the recurring leads keep one. The actor can — an
actor's name in a legend was typed by the owner and does not travel from role to role.

So the roster is keyed by role name, corroborated by the actor and weighted by how
many chapters the role appears in. Roles that recur with one settled actor are the
owner's own reference and belong in the author profile rather than being re-derived by
a model for every book.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field

from app.services.author_profile import normalize_name

# A legend writes «Роль - ?» where nobody is cast yet. Read literally it passes for a
# settled actor and the role reaches the reference list as though its casting were
# decided — nine of the first twenty-five did on the real corpus. An actor's name that
# merely carries a query — «Роман Сомов (?)» — is still a person, so only a bare
# placeholder counts as silence.
_UNCAST = {"?", "??", "???", "-", "—", "–", "tbd", "тбд", "не назначен"}


def _actor_name(raw: str) -> str:
    cleaned = (raw or "").strip()
    return "" if cleaned.casefold() in _UNCAST else cleaned


@dataclass
class RosterEntry:
    role: str
    variants: set[str] = field(default_factory=set)
    actors: collections.Counter = field(default_factory=collections.Counter)
    chapters: set[str] = field(default_factory=set)

    @property
    def actor(self) -> str:
        """The actor named most often, or "" if no legend ever named one."""
        named = [(name, count) for name, count in self.actors.items() if name]
        if not named:
            return ""
        return max(named, key=lambda pair: pair[1])[0]

    @property
    def actor_is_settled(self) -> bool:
        """Whether every legend that named an actor named the same one.

        Legends that named nobody do not count against it: the owner said some are
        simply missing the name, and silence is not disagreement.
        """
        named = {name for name in self.actors if name}
        return len(named) == 1

    @property
    def display_role(self) -> str:
        """The fullest spelling seen — «Куйбу Дегатти» over «Дегатти».

        A cast list wants the fuller form; the shorter spellings stay in `variants`
        so a script written either way still resolves to this role.
        """
        return max(self.variants, key=len) if self.variants else self.role


def build_roster(chapters) -> dict[str, RosterEntry]:
    """Roll the legends of many chapters into one cast, folded by name.

    Names fold by the profile's rule — stress marks dropped, «й» and «ё» kept whole —
    so «Дгарни́н» and «Дгарнин» are one role and not two.
    """
    roster: dict[str, RosterEntry] = {}
    for chapter in chapters:
        for entry in getattr(chapter, "legend", {}).values():
            role = (entry.role or "").strip()
            key = normalize_name(role)
            if not role or not key:
                continue
            item = roster.setdefault(key, RosterEntry(role=role))
            item.variants.add(role)
            item.actors[_actor_name(entry.actor)] += 1
            item.chapters.add(getattr(chapter, "title", "") or getattr(chapter, "path", ""))
    return roster


def merge_candidates(roster: dict[str, RosterEntry]) -> list[tuple[str, str]]:
    """Pairs of roles that look like one character under two spellings.

    Offered, never applied. The legends carry «Дегатти», «Куйбу Дегатти» and «Мэтр
    Дегатти» for one man, and the machine has no business deciding that: «Хилакток 2»
    and «Хилакток 3» stand in the same relation and are two different demons. The test
    is one name containing the other AND the same settled actor behind both — a strong
    hint and a weak proof, so it comes back as a question.
    """
    entries = [entry for entry in roster.values() if entry.actor and entry.actor_is_settled]
    pairs: list[tuple[str, str]] = []
    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            if left.actor != right.actor:
                continue
            a, b = normalize_name(left.display_role), normalize_name(right.display_role)
            if a != b and (a in b or b in a):
                pairs.append((left.display_role, right.display_role))
    return pairs


def settled_roles(roster: dict[str, RosterEntry], *, min_chapters: int = 2) -> list[RosterEntry]:
    """Entries worth treating as reference: recurring, with one undisputed actor.

    `min_chapters` is the guard against walk-ons — a role appearing in a single tale
    says nothing about the cycle. Most frequent first.
    """
    return sorted(
        (
            entry
            for entry in roster.values()
            if len(entry.chapters) >= min_chapters and entry.actor and entry.actor_is_settled
        ),
        key=lambda entry: (-len(entry.chapters), entry.display_role),
    )
