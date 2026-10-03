from app.pipeline.council_charext import (
    COUNCIL_CAST_SCHEMA,
    build_arbiter_prompt,
    build_participant_prompt,
)


def test_schema_is_well_formed_and_bounds_evidence():
    props = COUNCIL_CAST_SCHEMA["properties"]
    chars = props["characters"]["items"]["properties"]
    assert set(chars.keys()) == {"canonical", "aliases", "evidence"}
    assert chars["evidence"]["maxItems"] == 3


def test_participant_prompt_contains_text_and_strict_json_rule():
    system, user = build_participant_prompt("ВЕСЬ ТЕКСТ КНИГИ")
    assert "JSON" in system
    assert "проз" in system.lower()
    assert "ВЕСЬ ТЕКСТ КНИГИ" in user


def test_arbiter_prompt_includes_both_participant_casts_and_merge_rule():
    system, user = build_arbiter_prompt('{"characters":[{"canonical":"A"}]}', '{"characters":[{"canonical":"B"}]}')
    assert "сведи" in system.lower() or "арбитр" in system.lower()
    assert '"canonical":"A"' in user
    assert '"canonical":"B"' in user


def test_refine_prompt_contains_cast_and_cleanup_rule():
    from app.pipeline.council_charext import build_refine_prompt

    system, user = build_refine_prompt('{"characters":[{"canonical":"A","aliases":["x"]}]}')
    assert "JSON" in system
    assert "проз" in system.lower()
    assert '"canonical":"A"' in user
