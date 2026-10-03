from __future__ import annotations

from types import SimpleNamespace

from app.services.cast_budget import DEFAULT_ACTOR_RATE_RUB_PER_MIN, calc_character_total


def make_character(*, manual_rate_rub_per_min=None, manual_fixed_rub=None):
    return SimpleNamespace(
        manual_rate_rub_per_min=manual_rate_rub_per_min,
        manual_fixed_rub=manual_fixed_rub,
    )


def test_calc_character_total_uses_default_reference_rate_when_actor_rate_missing() -> None:
    character = make_character()

    total = calc_character_total(lines_count=1, approx_seconds=60, character=character, snapshot=None)

    assert total == DEFAULT_ACTOR_RATE_RUB_PER_MIN


def test_calc_character_total_prefers_fixed_budget_for_narrator_like_rows() -> None:
    character = make_character(manual_fixed_rub=4200)

    total = calc_character_total(lines_count=0, approx_seconds=0, character=character, snapshot=None)

    assert total == 4200
