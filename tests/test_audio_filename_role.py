"""A filename whose role cannot be read must say so, not invent one.

`_detect_role` fell back to "Narrator" whenever nothing in the name looked like a
character. That was harmless while every upload was scored the same way. It stopped
being harmless the moment narration became the one role that is accepted without a
coverage check: an unreadable filename would have been filed as narration and
approved without anything being verified.
"""
from app.services.audio_uploads import apply_batch_overrides, parse_batch_audio_filename


def _norm(value):
    return (value or "").strip()


def _parse(filename, **kwargs):
    return parse_batch_audio_filename(filename, normalize_role_label=_norm, **kwargs)


def test_the_actors_own_convention_parses_cleanly():
    parsed = _parse("KP_CH04_Химера_Иванова.wav")

    assert parsed["book_code"] == "KP"
    assert parsed["chapter"] == "Глава4"
    assert parsed["role"] == "Химера"
    assert parsed["actor_name"] == "Иванова"
    assert parsed["ok"]


def test_a_name_with_no_role_in_it_is_rejected_not_called_narration():
    parsed = _parse("KP_CH04.wav")

    assert parsed["role"] == ""
    assert "role_not_found" in parsed["errors"]
    assert not parsed["ok"]


def test_a_file_that_really_is_narration_keeps_the_role():
    parsed = _parse("KP_CH04_Рассказчик_Иванова.wav")

    assert parsed["role"] == "Рассказчик"
    assert parsed["ok"]


def test_an_override_without_a_role_is_an_error_not_a_default():
    """`role_required` was unreachable: the same "Narrator" default filled the gap first."""
    parsed = apply_batch_overrides(
        _parse("KP_CH04.wav"),
        {"book_code": "KP", "chapter": "Глава4", "role": "", "actor_name": "Иванова"},
        normalize_role_label=_norm,
    )

    assert parsed["role"] == ""
    assert "role_required" in parsed["errors"]
    assert not parsed["ok"]


def test_an_override_supplies_the_role_the_filename_lacked():
    parsed = apply_batch_overrides(
        _parse("KP_CH04.wav"),
        {"book_code": "KP", "chapter": "Глава4", "role": "Химера", "actor_name": "Иванова"},
        normalize_role_label=_norm,
    )

    assert parsed["role"] == "Химера"
    assert parsed["ok"]
