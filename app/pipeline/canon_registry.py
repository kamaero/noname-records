"""Cross-book canon registry reconcile (K1).

Conservative by design: LLM-built canon is a strong hypothesis, not absolute
truth, so we merge only on STRONG evidence (a shared canonical name-token, e.g.
'Гамук' / 'Гамук Ваньянвари') and flag everything else (alias overlap, possible
same, collisions) for the K1.5 operator gate. Better to under-merge and let the
operator merge than to over-merge and launder the error into the vector layer.

Pure — no DB. Storage/manifest live in scripts/build_canon_registry.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.character_names import _normalize_character_identity_key as _key


def _token_keys(name: str) -> set[str]:
    return {_key(t) for t in str(name or "").split() if _key(t)}


def _all_keys(canonical: str, aliases: list[str]) -> set[str]:
    return {k for k in ({_key(canonical)} | {_key(a) for a in aliases}) if k}


def _dedup_ci(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        k = _key(v)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(str(v).strip())
    return out


@dataclass
class CanonEntry:
    canonical: str
    aliases: list[str] = field(default_factory=list)
    source_books: list[str] = field(default_factory=list)
    appears_count: int = 0
    confidence: float = 0.0
    merge_status: str = "new"  # new | same_person | possible_same | shared_title | name_collision
    review_required: bool = False
    authority_source: str = "council_inferred"  # reference_doc | council_inferred | operator


def reconcile_book_into_registry(
    registry: list[CanonEntry],
    book_cast: list[Any],
    book_name: str,
    *,
    authority: str = "council_inferred",
) -> tuple[list[CanonEntry], list[tuple]]:
    """Fold one book's cast into the accumulating registry. Returns (registry, decisions)."""
    decisions: list[tuple] = []
    for inc in book_cast:
        inc_aliases = list(getattr(inc, "aliases", []) or [])
        inc_keys = _all_keys(inc.canonical, inc_aliases)
        inc_tokens = _token_keys(inc.canonical)

        strong: CanonEntry | None = None
        weak: list[CanonEntry] = []
        for e in registry:
            if inc_tokens & _token_keys(e.canonical):
                strong = e
                break
            if inc_keys & _all_keys(e.canonical, e.aliases):
                weak.append(e)

        if strong is not None:
            strong.aliases = _dedup_ci(strong.aliases + inc_aliases)
            if book_name not in strong.source_books:
                strong.source_books.append(book_name)
            strong.appears_count += 1
            strong.confidence = min(1.0, round(strong.confidence + 0.2, 3))
            strong.merge_status = "same_person"
            if authority == "reference_doc":
                strong.authority_source = "reference_doc"
            decisions.append(("same_person", inc.canonical, strong.canonical))
        elif weak:
            registry.append(
                CanonEntry(
                    canonical=inc.canonical,
                    aliases=_dedup_ci(inc_aliases),
                    source_books=[book_name],
                    appears_count=1,
                    confidence=0.4,
                    merge_status="possible_same",
                    review_required=True,
                    authority_source=authority,
                )
            )
            decisions.append(("possible_same", inc.canonical, [w.canonical for w in weak]))
        else:
            registry.append(
                CanonEntry(
                    canonical=inc.canonical,
                    aliases=_dedup_ci(inc_aliases),
                    source_books=[book_name],
                    appears_count=1,
                    confidence=0.5,
                    merge_status="new",
                    review_required=False,
                    authority_source=authority,
                )
            )
            decisions.append(("new", inc.canonical, None))
    return registry, decisions
