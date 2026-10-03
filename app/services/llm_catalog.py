from typing import Any

from app.constants import LLM_PRICING, LLM_PROVIDERS, MODEL_PRICING_RUB, MODEL_PROVIDER_HINTS


def calc_llm_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    return round(
        (max(0, int(prompt_tokens or 0)) / 1_000_000.0) * LLM_PRICING["input_per_million_usd"]
        + (max(0, int(completion_tokens or 0)) / 1_000_000.0) * LLM_PRICING["output_per_million_usd"],
        6,
    )


