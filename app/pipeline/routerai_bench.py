"""Pure helpers for the Ф0 routerai candidate benchmark.

No network, no DB — turns raw call numbers into a verdict + a markdown table,
so the logic is unit-testable. The runnable benchmark lives in
scripts/smoke_routerai_candidates.py.
"""
from __future__ import annotations

from dataclasses import dataclass

RUNAWAY_TRUNCATION_MARKER = "truncated at max_tokens"


@dataclass
class CandidateResult:
    name: str
    provider: str
    model: str
    ok: bool
    latency_s: float
    input_tokens: int
    output_tokens: int
    ratio: float
    runaway: bool
    error: str = ""


def evaluate_candidate(
    *,
    name: str,
    provider: str,
    model: str,
    content: str,
    usage: dict,
    latency_s: float,
    error: str = "",
    runaway_output_floor: int,
    runaway_ratio: float,
) -> CandidateResult:
    in_tokens = int((usage or {}).get("prompt_tokens") or 0)
    out_tokens = int((usage or {}).get("completion_tokens") or 0)
    ratio = round(out_tokens / max(1, in_tokens), 2)
    ok = bool(content) and not error
    runaway = (
        (bool(error) and RUNAWAY_TRUNCATION_MARKER in error)
        or out_tokens >= runaway_output_floor
        or ratio >= runaway_ratio
    )
    return CandidateResult(
        name=name,
        provider=provider,
        model=model,
        ok=ok,
        latency_s=round(latency_s, 2),
        input_tokens=in_tokens,
        output_tokens=out_tokens,
        ratio=ratio,
        runaway=runaway,
        error=error,
    )


def format_candidates_table(results: list[CandidateResult]) -> str:
    header = (
        "| Кандидат | provider/model | JSON | latency,s | in→out tok | ratio | runaway |"
    )
    sep = "|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for r in results:
        lines.append(
            f"| {r.name} | {r.provider}/{r.model} | {'✓' if r.ok else '✗'} | "
            f"{r.latency_s:.1f} | {r.input_tokens}→{r.output_tokens} | {r.ratio:.1f} | "
            f"{'⚠ ДА' if r.runaway else 'нет'} |"
        )
    return "\n".join(lines)
