"""A colour a person picked is rendered as picked.

`build_character_style_maps` spreads auto-assigned colours so that two roles do not
come out identical. It used to do that to hand-picked colours too: violet chosen for
Пупип was stored in the database and then replaced at render time by whatever the
palette suggested, so the cast screen showed violet and the reader showed salmon and
nothing in the interface explained the difference.
"""
from app.models import Character
from app.services.character_colors import build_character_style_maps


def _character(name: str, bg: str = "", fg: str = "") -> Character:
    return Character(id=f"c-{name}", book_id="b1", name=name, char_map_id=f"m-{name}",
                     character_color=bg, character_text_color=fg,
                     character_font_weight="700" if bg else "", character_font_style="normal" if bg else "")


def test_a_chosen_colour_survives_a_neighbour_that_is_close_to_it():
    # two roles the operator deliberately gave nearly the same colour
    chars = [_character("Алдан", "#7B2FF7", "#FFFFFF"), _character("Бором", "#7B2FF8", "#FFFFFF")]

    bg, _text, _weight, _style = build_character_style_maps(chars)

    assert bg["Алдан"] == "#7B2FF7"
    assert bg["Бором"] == "#7B2FF8"
    assert [c.character_color for c in chars] == ["#7B2FF7", "#7B2FF8"]


def test_a_role_without_a_colour_still_gets_a_distinct_one():
    chars = [_character("Алдан", "#7B2FF7", "#FFFFFF"), _character("Бором")]

    bg, _t, _w, _s = build_character_style_maps(chars)

    assert bg["Алдан"] == "#7B2FF7"
    assert bg["Бором"] and bg["Бором"].lower() != "#7b2ff7"


def test_an_unreadable_pair_still_gets_readable_type():
    """Keeping the background is not keeping an unreadable text colour on top of it."""
    chars = [_character("Алдан", "#101010", "#111111")]

    bg, text, _w, _s = build_character_style_maps(chars)

    assert bg["Алдан"] == "#101010"
    assert text["Алдан"].lower() != "#111111"
