"""Pure council orchestration for the consilium pipeline.

No network, no DB — the LLM call is injected as ``run_fn``. Persistence lives in
app/services/council_shadow.py; char_extraction prompts/schema in
app/pipeline/council_charext.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class CharacterEntry:
    canonical: str
    aliases: list[str] = field(default_factory=list)
    evidence_count: int = 0
    appears_in: list[int] = field(default_factory=list)  # chapter indices (range-based recall)


@dataclass
class CastProposal:
    source: str
    characters: list[CharacterEntry] = field(default_factory=list)


def _dedup_ci(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        key = str(v).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(str(v).strip())
    return out


def normalize_cast_output(
    source: str, raw_parsed: dict[str, Any], chapter_index: int | None = None
) -> CastProposal:
    chars: list[CharacterEntry] = []
    for item in (raw_parsed or {}).get("characters") or []:
        if not isinstance(item, dict):
            continue
        canonical = str(item.get("canonical") or "").strip()
        if not canonical:
            continue
        aliases = _dedup_ci([str(a) for a in (item.get("aliases") or []) if str(a).strip()])
        evidence_count = len([e for e in (item.get("evidence") or []) if str(e).strip()])
        appears_in = [int(chapter_index)] if chapter_index is not None else []
        chars.append(
            CharacterEntry(
                canonical=canonical,
                aliases=aliases,
                evidence_count=evidence_count,
                appears_in=appears_in,
            )
        )
    return CastProposal(source=source, characters=chars)


def merge_cast_proposals(source: str, proposals: list[CastProposal]) -> CastProposal:
    """Merge per-chapter casts into one: dedup by canonical (case-insensitive),
    union aliases, union appears_in (chapter indices), sum evidence_count."""
    by_canon: dict[str, CharacterEntry] = {}
    for p in proposals:
        for c in p.characters:
            key = c.canonical.strip().lower()
            if not key:
                continue
            acc = by_canon.get(key)
            if acc is None:
                by_canon[key] = CharacterEntry(
                    canonical=c.canonical,
                    aliases=list(c.aliases),
                    evidence_count=c.evidence_count,
                    appears_in=list(c.appears_in),
                )
            else:
                acc.aliases = _dedup_ci(acc.aliases + c.aliases)
                acc.evidence_count += c.evidence_count
                acc.appears_in = sorted(set(acc.appears_in) | set(c.appears_in))
    for acc in by_canon.values():
        acc.appears_in = sorted(set(acc.appears_in))
    return CastProposal(source=source, characters=list(by_canon.values()))


def _canon_set(p: CastProposal) -> set[str]:
    return {c.canonical.strip().lower() for c in p.characters if c.canonical.strip()}


def _alias_owner_map(p: CastProposal) -> dict[str, str]:
    owners: dict[str, str] = {}
    for c in p.characters:
        for a in c.aliases:
            owners[a.strip().lower()] = c.canonical.strip().lower()
    return owners


def cast_disagreement(a: CastProposal, b: CastProposal) -> dict[str, Any]:
    """Deterministic distance between two cast proposals (the central metric)."""
    set_a, set_b = _canon_set(a), _canon_set(b)
    only_a = sorted(c.canonical for c in a.characters if c.canonical.strip().lower() in (set_a - set_b))
    only_b = sorted(c.canonical for c in b.characters if c.canonical.strip().lower() in (set_b - set_a))
    cast_diff = len(only_a) + len(only_b)

    owners_a, owners_b = _alias_owner_map(a), _alias_owner_map(b)
    shared_aliases = set(owners_a) & set(owners_b)
    alias_diff = sum(1 for al in shared_aliases if owners_a[al] != owners_b[al])

    scalar = cast_diff + alias_diff
    return {
        "cast_diff": cast_diff,
        "alias_diff": alias_diff,
        "scalar": scalar,
        "only_in_a": only_a,
        "only_in_b": only_b,
    }


# (text, model) call contract: run_fn(provider, model, system, user, schema) -> parsed dict
RunFn = Callable[[str, str, str, str, dict], dict[str, Any]]

# import here to keep council.py free of any heavy deps at module top
from app.pipeline.council_charext import (  # noqa: E402
    build_arbiter_prompt,
    build_participant_prompt,
    build_refine_prompt,
    COUNCIL_CAST_SCHEMA,
)
import json  # noqa: E402


def run_council_shadow(
    *,
    book_text: str,
    members: list[tuple[str, str, str]],
    arbiter: tuple[str, str, str],
    run_fn: RunFn,
) -> dict[str, Any]:
    """One shadow round: each member extracts a cast, the arbiter merges them.

    members/arbiter items are (display_name, provider, model). Returns participant
    CastProposals, the arbiter CastProposal, the pairwise disagreement of the first
    two members, and per-source errors. Pure — no DB writes here.
    """
    system_p, user_p = build_participant_prompt(book_text)
    participants: list[CastProposal] = []
    errors: dict[str, str] = {}
    raw_by_source: dict[str, dict[str, Any]] = {}
    for name, provider, model in members:
        try:
            parsed = run_fn(provider, model, system_p, user_p, COUNCIL_CAST_SCHEMA)
        except Exception as exc:  # noqa: BLE001 — one bad member must not kill the round
            errors[name] = str(exc)[:300]
            parsed = {"characters": []}
        raw_by_source[name] = parsed
        participants.append(normalize_cast_output(name, parsed))

    disagreement = (
        cast_disagreement(participants[0], participants[1]) if len(participants) >= 2 else {}
    )

    a_json = json.dumps(raw_by_source.get(members[0][0], {}), ensure_ascii=False)
    b_json = json.dumps(raw_by_source.get(members[1][0], {}), ensure_ascii=False) if len(members) >= 2 else "{}"
    system_a, user_a = build_arbiter_prompt(a_json, b_json)
    arb_name, arb_provider, arb_model = arbiter
    try:
        arb_parsed = run_fn(arb_provider, arb_model, system_a, user_a, COUNCIL_CAST_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        errors[arb_name] = str(exc)[:300]
        arb_parsed = {"characters": []}

    return {
        "participants": participants,
        "arbiter": normalize_cast_output(arb_name, arb_parsed),
        "disagreement": disagreement,
        "errors": errors,
        "raw": {**raw_by_source, arb_name: arb_parsed},
    }


def guard_arbiter_merges(
    arbiter: CastProposal, recall: CastProposal
) -> tuple[CastProposal, list[dict[str, Any]]]:
    """Split out arbiter aliases that are themselves a DISTINCT recall character's
    canonical — a likely wrong identity merge (e.g. folding 'Сатухух' under 'Дгарнин').

    Conservative + token-aware: only split an alias that (a) is itself a recall
    canonical AND (b) shares NO name-token with its canonical — i.e. a different
    proper name ('Сатухух' under 'Дгарнин'), NOT a same-person variant ('Гамук'
    under 'Гамук Ваньянвари', which share the token 'Гамук'). The offending alias
    is removed and, if missing, restored as its own character.
    Returns (guarded_proposal, splits).
    """
    import re as _re

    from app.services.character_names import _normalize_character_identity_key as _key

    def _token_keys(value: str) -> set[str]:
        return {_key(t) for t in _re.split(r"\s+", str(value or "").strip()) if _key(t)}

    recall_canon: dict[str, str] = {}
    for c in recall.characters:
        k = _key(c.canonical)
        if k:
            recall_canon[k] = c.canonical

    out: list[CharacterEntry] = []
    splits: list[dict[str, Any]] = []
    present: set[str] = set()
    for c in arbiter.characters:
        ckey = _key(c.canonical)
        c_tokens = _token_keys(c.canonical)
        kept: list[str] = []
        for a in c.aliases:
            ak = _key(a)
            shares_token = bool(_token_keys(a) & c_tokens)
            if ak and ak != ckey and ak in recall_canon and not shares_token:
                splits.append({"from": c.canonical, "alias": a, "restored": recall_canon[ak]})
            else:
                kept.append(a)
        out.append(
            CharacterEntry(
                canonical=c.canonical,
                aliases=kept,
                evidence_count=c.evidence_count,
                appears_in=list(c.appears_in),
            )
        )
        present.add(ckey)

    for s in splits:
        rk = _key(s["restored"])
        if rk and rk not in present:
            out.append(CharacterEntry(canonical=s["restored"]))
            present.add(rk)

    return CastProposal(source=arbiter.source, characters=out), splits


def run_council_charext_chunked(
    *,
    chapters: list[tuple[int, str]],
    recaller: tuple[str, str, str],
    arbiter: tuple[str, str, str],
    run_fn: RunFn,
) -> dict[str, Any]:
    """Range-based recall: one recaller extracts the cast per chapter, results are
    merged (with appears_in chapter indices), and the arbiter cleans/merges the
    single merged cast. Returns a shape compatible with persist_council_shadow:
    participants=[merged_recall] (carries appears_in), arbiter=cleaned proposal,
    disagreement=delta(merged -> arbiter). Pure — no DB writes here.
    """
    rec_name, rec_provider, rec_model = recaller
    per_chapter: list[CastProposal] = []
    errors: dict[str, str] = {}
    for idx, text in chapters:
        system_p, user_p = build_participant_prompt(text)
        try:
            parsed = run_fn(rec_provider, rec_model, system_p, user_p, COUNCIL_CAST_SCHEMA)
        except Exception as exc:  # noqa: BLE001 — one bad chapter must not kill the run
            errors[f"chapter:{idx}"] = str(exc)[:300]
            parsed = {"characters": []}
        per_chapter.append(normalize_cast_output(rec_name, parsed, chapter_index=idx))

    merged = merge_cast_proposals(rec_name, per_chapter)

    merged_json = json.dumps(
        {"characters": [{"canonical": c.canonical, "aliases": c.aliases} for c in merged.characters]},
        ensure_ascii=False,
    )
    system_a, user_a = build_refine_prompt(merged_json)
    arb_name, arb_provider, arb_model = arbiter
    try:
        arb_parsed = run_fn(arb_provider, arb_model, system_a, user_a, COUNCIL_CAST_SCHEMA)
    except Exception as exc:  # noqa: BLE001
        errors[arb_name] = str(exc)[:300]
        arb_parsed = {"characters": []}
    arbiter_prop = normalize_cast_output(arb_name, arb_parsed)
    arbiter_prop, guard_splits = guard_arbiter_merges(arbiter_prop, merged)

    return {
        "participants": [merged],
        "arbiter": arbiter_prop,
        "disagreement": cast_disagreement(merged, arbiter_prop),
        "guard_splits": guard_splits,
        "errors": errors,
        "raw": {},
    }
