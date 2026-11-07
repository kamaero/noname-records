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


def test_generated_role_description_uses_the_requested_book_language():
    from app.services.cast_budget import promote_role_into_cast

    for language, description in [("en", "chapter character"), ("ru", "персонаж главы")]:
        content, changed = promote_role_into_cast("Source book text.", "Aster", language=language)
        assert changed and f"[Aster] :: {description}" in content
        assert content.endswith("Source book text.")
    default, _ = promote_role_into_cast("", "Aster")
    assert "персонаж главы" in default


def test_role_defaults_preserve_existing_notes_and_explicit_source_descriptions():
    from app.services.cast_budget import promote_role_into_cast

    source = "[CAST]\n[Aster] :: Пользовательское описание\n\nSource text."
    assert promote_role_into_cast(source, "Aster", language="en") == (source, False)
    created, changed = promote_role_into_cast("", "Aster", "Точное описание автора", language="en")
    assert changed and "Точное описание автора" in created
