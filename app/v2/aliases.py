"""Match a role name written one way against the cast that writes it another.

The benchmark measures whether a model found the right speaker, not whether it chose
the same spelling as the workbook, so «Дегатти» must not be scored wrong against
«Куйбу Дегатти». Four rules, each accepted separately by the owner on measured
evidence over the whole corpus:

  * an explicit merge list, for what only a person can decide (477 spans);
  * drop a parenthetical — «Коруак (управляющий Банка Душ)» is «Коруак» (396 spans);
  * drop a «Рассказчик» prefix — «Рассказчик Инвнехлизад» is a tale he tells (130);
  * accept a containment only when exactly one cast signature contains the name (581).

That last rule is deliberately narrow. «Дегатти» sits inside both «Куйбу Дегатти» and
«Мэтр Дегатти», so it resolves to neither by machine, and the owner settled that
family by hand. Widening it to a best guess would fuse «Хилакток 2» with «Хилакток 3».
"""
from __future__ import annotations

import collections
import re

from app.services.author_profile import normalize_name

NARRATOR = "Рассказчик"

# Settled by the owner: one man, four spellings.
DEGATTI = ("Куйбу Дегатти", ["Дегатти", "Мэтр Дегатти", "Куйбу Дегатти", "Куйбу"])

_PARENTHETICAL = re.compile(r"\s*\([^)]*\)\s*")
_NARRATOR_PREFIX = re.compile(rf"^\s*{NARRATOR}\s+", re.IGNORECASE)


class AliasResolver:
    """Resolve a written role name to a canonical cast signature, or to None.

    None means "the cast does not know this name", which is a fact worth reporting
    rather than a failure worth hiding: `unresolved` counts every name that did not
    land, so a benchmark run can say how much of its input it could not place.
    """

    def __init__(self, *, cast: dict[str, str], merges=()):
        self._cast = dict(cast)
        self._explicit: dict[str, str] = {}
        for canonical, spellings in merges:
            for spelling in spellings:
                key = normalize_name(spelling)
                if key:
                    self._explicit[key] = canonical
        self.unresolved: collections.Counter = collections.Counter()

    def _lookup(self, name: str) -> str | None:
        key = normalize_name(name)
        if not key:
            return None
        if key in self._explicit:
            return self._explicit[key]
        if key in self._cast:
            return self._cast[key]
        return None

    def resolve(self, name: str | None) -> str | None:
        raw = (name or "").strip()
        if not raw:
            return None
        if normalize_name(raw) == normalize_name(NARRATOR):
            return NARRATOR

        for candidate in (
            raw,
            _PARENTHETICAL.sub(" ", raw).strip(),
            _NARRATOR_PREFIX.sub("", raw).strip(),
        ):
            found = self._lookup(candidate)
            if found:
                return found

        # Containment, and only when it is unambiguous.
        key = normalize_name(raw)
        hits = [signature for signature_key, signature in self._cast.items() if key in signature_key]
        if len(hits) == 1:
            return hits[0]

        self.unresolved[raw] += 1
        return None
